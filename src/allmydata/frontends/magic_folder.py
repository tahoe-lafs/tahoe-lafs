
import sys, os
import os.path
from collections import deque
import time

from twisted.internet import defer, reactor, task
from twisted.python.failure import Failure
from twisted.python import runtime
from twisted.application import service

from allmydata.util import fileutil
from allmydata.interfaces import IDirectoryNode
from allmydata.util import log
from allmydata.util.fileutil import precondition_abspath, get_pathinfo, ConflictError
from allmydata.util.assertutil import precondition, _assert
from allmydata.util.deferredutil import HookMixin
from allmydata.util.encodingutil import listdir_filepath, to_filepath, \
     extend_filepath, unicode_from_filepath, unicode_segments_from, \
     quote_filepath, quote_local_unicode_path, quote_output, FilenameEncodingError
from allmydata.immutable.upload import FileName, Data
from allmydata import magicfolderdb, magicpath


IN_EXCL_UNLINK = 0x04000000L

def get_inotify_module():
    try:
        if sys.platform == "win32":
            from allmydata.windows import inotify
        elif runtime.platform.supportsINotify():
            from twisted.internet import inotify
        else:
            raise NotImplementedError("filesystem notification needed for Magic Folder is not supported.\n"
                                      "This currently requires Linux or Windows.")
        return inotify
    except (ImportError, AttributeError) as e:
        log.msg(e)
        if sys.platform == "win32":
            raise NotImplementedError("filesystem notification needed for Magic Folder is not supported.\n"
                                      "Windows support requires at least Vista, and has only been tested on Windows 7.")
        raise


class MagicFolder(service.MultiService):
    name = 'magic-folder'

    def __init__(self, client, upload_dircap, collective_dircap, local_path_u, dbfile,
                 pending_delay=1.0, clock=reactor):
        precondition_abspath(local_path_u)

        service.MultiService.__init__(self)

        db = magicfolderdb.get_magicfolderdb(dbfile, create_version=(magicfolderdb.SCHEMA_v1, 1))
        if db is None:
            return Failure(Exception('ERROR: Unable to load magic folder db.'))

        # for tests
        self._client = client
        self._db = db

        self.is_ready = False

        self.uploader = Uploader(client, local_path_u, db, upload_dircap, pending_delay, clock)
        self.downloader = Downloader(client, local_path_u, db, collective_dircap, clock)

    def startService(self):
        # TODO: why is this being called more than once?
        if self.running:
            return defer.succeed(None)
        print "%r.startService" % (self,)
        service.MultiService.startService(self)
        return self.uploader.start_monitoring()

    def ready(self):
        """ready is used to signal us to start
        processing the upload and download items...
        """
        self.is_ready = True
        d = self.uploader.start_scanning()
        d2 = self.downloader.start_scanning()
        d.addCallback(lambda ign: d2)
        return d

    def finish(self):
        print "finish"
        d = self.uploader.stop()
        d2 = self.downloader.stop()
        d.addCallback(lambda ign: d2)
        return d

    def remove_service(self):
        return service.MultiService.disownServiceParent(self)


class QueueMixin(HookMixin):
    def __init__(self, client, local_path_u, db, name, clock):
        self._client = client
        self._local_path_u = local_path_u
        self._local_filepath = to_filepath(local_path_u)
        self._db = db
        self._name = name
        self._clock = clock
        self._hooks = {'processed': None, 'started': None}
        self.started_d = self.set_hook('started')

        if not self._local_filepath.exists():
            raise AssertionError("The '[magic_folder] local.directory' parameter was %s "
                                 "but there is no directory at that location."
                                 % quote_local_unicode_path(self._local_path_u))
        if not self._local_filepath.isdir():
            raise AssertionError("The '[magic_folder] local.directory' parameter was %s "
                                 "but the thing at that location is not a directory."
                                 % quote_local_unicode_path(self._local_path_u))

        self._deque = deque()
        self._lazy_tail = defer.succeed(None)
        self._pending = set()
        self._stopped = False
        self._turn_delay = 0

    def _get_filepath(self, relpath_u):
        return extend_filepath(self._local_filepath, relpath_u.split(u"/"))

    def _get_relpath(self, filepath):
        self._log("_get_relpath(%r)" % (filepath,))
        segments = unicode_segments_from(filepath, self._local_filepath)
        self._log("segments = %r" % (segments,))
        return u"/".join(segments)

    def _count(self, counter_name, delta=1):
        ctr = 'magic_folder.%s.%s' % (self._name, counter_name)
        self._log("%s += %r" % (counter_name, delta))
        self._client.stats_provider.count(ctr, delta)

    def _logcb(self, res, msg):
        self._log("%s: %r" % (msg, res))
        return res

    def _log(self, msg):
        s = "Magic Folder %s %s: %s" % (quote_output(self._client.nickname), self._name, msg)
        self._client.log(s)
        print s
        #open("events", "ab+").write(msg)

    def _append_to_deque(self, relpath_u):
        self._log("_append_to_deque(%r)" % (relpath_u,))
        if relpath_u in self._pending or magicpath.should_ignore_file(relpath_u):
            return
        self._deque.append(relpath_u)
        self._pending.add(relpath_u)
        self._count('objects_queued')
        if self.is_ready:
            self._clock.callLater(0, self._turn_deque)

    def _turn_deque(self):
        self._log("_turn_deque")
        if self._stopped:
            self._log("stopped")
            return
        try:
            item = self._deque.pop()
            self._log("popped %r" % (item,))
            self._count('objects_queued', -1)
        except IndexError:
            self._log("deque is now empty")
            self._lazy_tail.addCallback(lambda ign: self._when_queue_is_empty())
        else:
            self._lazy_tail.addCallback(lambda ign: self._process(item))
            self._lazy_tail.addBoth(self._call_hook, 'processed')
            self._lazy_tail.addErrback(log.err)
            self._lazy_tail.addCallback(lambda ign: task.deferLater(self._clock, self._turn_delay, self._turn_deque))


class Uploader(QueueMixin):
    def __init__(self, client, local_path_u, db, upload_dircap, pending_delay, clock):
        QueueMixin.__init__(self, client, local_path_u, db, 'uploader', clock)

        self.is_ready = False

        # TODO: allow a path rather than a cap URI.
        self._upload_dirnode = self._client.create_node_from_uri(upload_dircap)
        if not IDirectoryNode.providedBy(self._upload_dirnode):
            raise AssertionError("The URI in '%s' does not refer to a directory."
                                 % os.path.join('private', 'magic_folder_dircap'))
        if self._upload_dirnode.is_unknown() or self._upload_dirnode.is_readonly():
            raise AssertionError("The URI in '%s' is not a writecap to a directory."
                                 % os.path.join('private', 'magic_folder_dircap'))

        self._inotify = get_inotify_module()
        self._notifier = self._inotify.INotify()

        if hasattr(self._notifier, 'set_pending_delay'):
            self._notifier.set_pending_delay(pending_delay)

        # TODO: what about IN_MOVE_SELF, IN_MOVED_FROM, or IN_UNMOUNT?
        #
        self.mask = ( self._inotify.IN_CREATE
                    | self._inotify.IN_CLOSE_WRITE
                    | self._inotify.IN_MOVED_TO
                    | self._inotify.IN_MOVED_FROM
                    | self._inotify.IN_DELETE
                    | self._inotify.IN_ONLYDIR
                    | IN_EXCL_UNLINK
                    )
        self._notifier.watch(self._local_filepath, mask=self.mask, callbacks=[self._notify],
                             recursive=True)

    def start_monitoring(self):
        self._log("start_monitoring")
        d = defer.succeed(None)
        d.addCallback(lambda ign: self._notifier.startReading())
        d.addCallback(lambda ign: self._count('dirs_monitored'))
        d.addBoth(self._call_hook, 'started')
        return d

    def stop(self):
        self._log("stop")
        self._notifier.stopReading()
        self._count('dirs_monitored', -1)
        if hasattr(self._notifier, 'wait_until_stopped'):
            d = self._notifier.wait_until_stopped()
        else:
            d = defer.succeed(None)
        d.addCallback(lambda ign: self._lazy_tail)
        return d

    def start_scanning(self):
        self._log("start_scanning")
        self.is_ready = True
        self._pending = self._db.get_all_relpaths()
        self._log("all_files %r" % (self._pending))
        d = self._scan(u"")
        def _add_pending(ign):
            # This adds all of the files that were in the db but not already processed
            # (normally because they have been deleted on disk).
            self._log("adding %r" % (self._pending))
            self._deque.extend(self._pending)
        d.addCallback(_add_pending)
        d.addCallback(lambda ign: self._turn_deque())
        return d

    def _scan(self, reldir_u):
        self._log("scan %r" % (reldir_u,))
        fp = self._get_filepath(reldir_u)
        try:
            children = listdir_filepath(fp)
        except EnvironmentError:
            raise Exception("WARNING: magic folder: permission denied on directory %s"
                            % quote_filepath(fp))
        except FilenameEncodingError:
            raise Exception("WARNING: magic folder: could not list directory %s due to a filename encoding error"
                            % quote_filepath(fp))

        d = defer.succeed(None)
        for child in children:
            _assert(isinstance(child, unicode), child=child)
            d.addCallback(lambda ign, child=child:
                          ("%s/%s" % (reldir_u, child) if reldir_u else child))
            def _add_pending(relpath_u):
                if magicpath.should_ignore_file(relpath_u):
                    return None

                self._pending.add(relpath_u)
                return relpath_u
            d.addCallback(_add_pending)
            # This call to _process doesn't go through the deque, and probably should.
            d.addCallback(self._process)
            d.addBoth(self._call_hook, 'processed')
            d.addErrback(log.err)

        return d

    def _notify(self, opaque, path, events_mask):
        self._log("inotify event %r, %r, %r\n" % (opaque, path, ', '.join(self._inotify.humanReadableMask(events_mask))))

        # We filter out IN_CREATE events not associated with a directory.
        # Acting on IN_CREATE for files could cause us to read and upload
        # a possibly-incomplete file before the application has closed it.
        # There should always be an IN_CLOSE_WRITE after an IN_CREATE, I think.
        # It isn't possible to avoid watching for IN_CREATE at all, because
        # it is the only event notified for a directory creation.

        if ((events_mask & self._inotify.IN_CREATE) != 0 and
            (events_mask & self._inotify.IN_ISDIR) == 0):
            self._log("ignoring inotify event for creation of file %r\n" % (path,))
            return

        relpath_u = self._get_relpath(path)
        self._append_to_deque(relpath_u)

    def _when_queue_is_empty(self):
        return defer.succeed(None)

    def _process(self, relpath_u):
        self._log("_process(%r)" % (relpath_u,))
        if relpath_u is None:
            return
        precondition(isinstance(relpath_u, unicode), relpath_u)

        d = defer.succeed(None)

        def _maybe_upload(val, now=None):
            if now is None:
                now = time.time()
            fp = self._get_filepath(relpath_u)
            pathinfo = get_pathinfo(unicode_from_filepath(fp))

            self._log("pending = %r, about to remove %r" % (self._pending, relpath_u))
            self._pending.remove(relpath_u)
            encoded_path_u = magicpath.path2magic(relpath_u)

            if not pathinfo.exists:
                # FIXME merge this with the 'isfile' case.
                self._log("notified object %s disappeared (this is normal)" % quote_filepath(fp))
                self._count('objects_disappeared')
                if not self._db.check_file_db_exists(relpath_u):
                    return None

                last_downloaded_timestamp = now
                last_downloaded_uri = self._db.get_last_downloaded_uri(relpath_u)

                current_version = self._db.get_local_file_version(relpath_u)
                if current_version is None:
                    new_version = 0
                elif self._db.is_new_file(pathinfo, relpath_u):
                    new_version = current_version + 1
                else:
                    self._log("Not uploading %r" % (relpath_u,))
                    self._count('objects_not_uploaded')
                    return

                metadata = { 'version': new_version,
                             'deleted': True,
                             'last_downloaded_timestamp': last_downloaded_timestamp }
                if last_downloaded_uri is not None:
                    metadata['last_downloaded_uri'] = last_downloaded_uri

                empty_uploadable = Data("", self._client.convergence)
                d2 = self._upload_dirnode.add_file(encoded_path_u, empty_uploadable,
                                                   metadata=metadata, overwrite=True)

                def _add_db_entry(filenode):
                    filecap = filenode.get_uri()
                    self._db.did_upload_version(relpath_u, new_version, filecap,
                                                last_downloaded_uri, last_downloaded_timestamp, pathinfo)
                    self._count('files_uploaded')
                d2.addCallback(_add_db_entry)
                return d2
            elif pathinfo.islink:
                self.warn("WARNING: cannot upload symlink %s" % quote_filepath(fp))
                return None
            elif pathinfo.isdir:
                self._notifier.watch(fp, mask=self.mask, callbacks=[self._notify], recursive=True)
                uploadable = Data("", self._client.convergence)
                encoded_path_u += magicpath.path2magic(u"/")
                upload_d = self._upload_dirnode.add_file(encoded_path_u, uploadable, metadata={"version":0}, overwrite=True)
                def _succeeded(ign):
                    self._log("created subdirectory %r" % (relpath_u,))
                    self._count('directories_created')
                def _failed(f):
                    self._log("failed to create subdirectory %r" % (relpath_u,))
                    return f
                upload_d.addCallbacks(_succeeded, _failed)
                upload_d.addCallback(lambda ign: self._scan(relpath_u))
                return upload_d
            elif pathinfo.isfile:
                last_downloaded_uri = self._db.get_last_downloaded_uri(relpath_u)
                last_downloaded_timestamp = now

                current_version = self._db.get_local_file_version(relpath_u)
                if current_version is None:
                    new_version = 0
                elif self._db.is_new_file(pathinfo, relpath_u):
                    new_version = current_version + 1
                else:
                    self._log("Not uploading %r" % (relpath_u,))
                    self._count('objects_not_uploaded')
                    return None

                metadata = { 'version': new_version,
                             'last_downloaded_timestamp': last_downloaded_timestamp }
                if last_downloaded_uri is not None:
                    metadata['last_downloaded_uri'] = last_downloaded_uri

                uploadable = FileName(unicode_from_filepath(fp), self._client.convergence)
                d2 = self._upload_dirnode.add_file(encoded_path_u, uploadable,
                                                   metadata=metadata, overwrite=True)

                def _add_db_entry(filenode):
                    filecap = filenode.get_uri()
                    last_downloaded_uri = metadata.get('last_downloaded_uri', None)
                    self._db.did_upload_version(relpath_u, new_version, filecap,
                                                last_downloaded_uri, last_downloaded_timestamp, pathinfo)
                    self._count('files_uploaded')
                d2.addCallback(_add_db_entry)
                return d2
            else:
                self.warn("WARNING: cannot process special file %s" % quote_filepath(fp))
                return None

        d.addCallback(_maybe_upload)

        def _succeeded(res):
            self._count('objects_succeeded')
            return res
        def _failed(f):
            self._count('objects_failed')
            self._log("%s while processing %r" % (f, relpath_u))
            return f
        d.addCallbacks(_succeeded, _failed)
        return d

    def _get_metadata(self, encoded_path_u):
        try:
            d = self._upload_dirnode.get_metadata_for(encoded_path_u)
        except KeyError:
            return Failure()
        return d

    def _get_filenode(self, encoded_path_u):
        try:
            d = self._upload_dirnode.get(encoded_path_u)
        except KeyError:
            return Failure()
        return d


class WriteFileMixin(object):
    FUDGE_SECONDS = 10.0

    def _get_conflicted_filename(self, abspath_u):
        return abspath_u + u".conflict"

    def _write_downloaded_file(self, abspath_u, file_contents, is_conflict=False, now=None):
        self._log("_write_downloaded_file(%r, <%d bytes>, is_conflict=%r, now=%r)"
                  % (abspath_u, len(file_contents), is_conflict, now))

        # 1. Write a temporary file, say .foo.tmp.
        # 2. is_conflict determines whether this is an overwrite or a conflict.
        # 3. Set the mtime of the replacement file to be T seconds before the
        #    current local time.
        # 4. Perform a file replacement with backup filename foo.backup,
        #    replaced file foo, and replacement file .foo.tmp. If any step of
        #    this operation fails, reclassify as a conflict and stop.
        #
        # Returns the path of the destination file.

        precondition_abspath(abspath_u)
        replacement_path_u = abspath_u + u".tmp"  # FIXME more unique
        backup_path_u = abspath_u + u".backup"
        if now is None:
            now = time.time()

        # ensure parent directory exists
        head, tail = os.path.split(abspath_u)
        mode = 0777 # XXX
        fileutil.make_dirs(head, mode)

        fileutil.write(replacement_path_u, file_contents)
        os.utime(replacement_path_u, (now, now - self.FUDGE_SECONDS))
        if is_conflict:
            print "0x00 ------------ <><> is conflict; calling _rename_conflicted_file... %r %r" % (abspath_u, replacement_path_u)
            return self._rename_conflicted_file(abspath_u, replacement_path_u)
        else:
            try:
                fileutil.replace_file(abspath_u, replacement_path_u, backup_path_u)
                return abspath_u
            except fileutil.ConflictError:
                return self._rename_conflicted_file(abspath_u, replacement_path_u)

    def _rename_conflicted_file(self, abspath_u, replacement_path_u):
        self._log("_rename_conflicted_file(%r, %r)" % (abspath_u, replacement_path_u))

        conflict_path_u = self._get_conflicted_filename(abspath_u)
        print "XXX rename %r %r" % (replacement_path_u, conflict_path_u)
        if os.path.isfile(replacement_path_u):
            print "%r exists" % (replacement_path_u,)
        if os.path.isfile(conflict_path_u):
            print "%r exists" % (conflict_path_u,)

        fileutil.rename_no_overwrite(replacement_path_u, conflict_path_u)
        return conflict_path_u

    def _rename_deleted_file(self, abspath_u):
        self._log('renaming deleted file to backup: %s' % (abspath_u,))
        try:
            fileutil.rename_no_overwrite(abspath_u, abspath_u + u'.backup')
        except IOError:
            # XXX is this the correct error?
            self._log("Already gone: '%s'" % (abspath_u,))
        return abspath_u


class Downloader(QueueMixin, WriteFileMixin):
    REMOTE_SCAN_INTERVAL = 3  # facilitates tests

    def __init__(self, client, local_path_u, db, collective_dircap, clock):
        QueueMixin.__init__(self, client, local_path_u, db, 'downloader', clock)

        # TODO: allow a path rather than a cap URI.
        self._collective_dirnode = self._client.create_node_from_uri(collective_dircap)

        if not IDirectoryNode.providedBy(self._collective_dirnode):
            raise AssertionError("The URI in '%s' does not refer to a directory."
                                 % os.path.join('private', 'collective_dircap'))
        if self._collective_dirnode.is_unknown() or not self._collective_dirnode.is_readonly():
            raise AssertionError("The URI in '%s' is not a readonly cap to a directory."
                                 % os.path.join('private', 'collective_dircap'))

        self._turn_delay = self.REMOTE_SCAN_INTERVAL
        self._download_scan_batch = {} # path -> [(filenode, metadata)]

    def start_scanning(self):
        self._log("start_scanning")
        files = self._db.get_all_relpaths()
        self._log("all files %s" % files)

        d = self._scan_remote_collective()
        self._turn_deque()
        return d

    def stop(self):
        self._stopped = True
        d = defer.succeed(None)
        d.addCallback(lambda ign: self._lazy_tail)
        return d

    def _should_download(self, relpath_u, remote_version):
        """
        _should_download returns a bool indicating whether or not a remote object should be downloaded.
        We check the remote metadata version against our magic-folder db version number;
        latest version wins.
        """
        self._log("_should_download(%r, %r)" % (relpath_u, remote_version))
        if magicpath.should_ignore_file(relpath_u):
            self._log("nope")
            return False
        self._log("yep")
        v = self._db.get_local_file_version(relpath_u)
        self._log("v = %r" % (v,))
        return (v is None or v < remote_version)

    def _get_local_latest(self, relpath_u):
        """
        _get_local_latest takes a unicode path string checks to see if this file object
        exists in our magic-folder db; if not then return None
        else check for an entry in our magic-folder db and return the version number.
        """
        if not self._get_filepath(relpath_u).exists():
            return None
        return self._db.get_local_file_version(relpath_u)

    def _get_collective_latest_file(self, filename):
        """
        _get_collective_latest_file takes a file path pointing to a file managed by
        magic-folder and returns a deferred that fires with the two tuple containing a
        file node and metadata for the latest version of the file located in the
        magic-folder collective directory.
        """
        collective_dirmap_d = self._collective_dirnode.list()
        def scan_collective(result):
            list_of_deferreds = []
            for dir_name in result.keys():
                # XXX make sure it's a directory
                d = defer.succeed(None)
                d.addCallback(lambda x, dir_name=dir_name: result[dir_name][0].get_child_and_metadata(filename))
                list_of_deferreds.append(d)
            deferList = defer.DeferredList(list_of_deferreds, consumeErrors=True)
            return deferList
        collective_dirmap_d.addCallback(scan_collective)
        def highest_version(deferredList):
            max_version = 0
            metadata = None
            node = None
            for success, result in deferredList:
                if success:
                    if result[1]['version'] > max_version:
                        node, metadata = result
                        max_version = result[1]['version']
            return node, metadata
        collective_dirmap_d.addCallback(highest_version)
        return collective_dirmap_d

    def _append_to_batch(self, name, file_node, metadata):
        if self._download_scan_batch.has_key(name):
            self._download_scan_batch[name] += [(file_node, metadata)]
        else:
            self._download_scan_batch[name] = [(file_node, metadata)]

    def _scan_remote(self, nickname, dirnode):
        self._log("_scan_remote nickname %r" % (nickname,))
        d = dirnode.list()
        def scan_listing(listing_map):
            for encoded_relpath_u in listing_map.keys():
                relpath_u = magicpath.magic2path(encoded_relpath_u)
                self._log("found %r" % (relpath_u,))

                file_node, metadata = listing_map[encoded_relpath_u]
                local_version = self._get_local_latest(relpath_u)
                remote_version = metadata.get('version', None)
                self._log("%r has local version %r, remote version %r" % (relpath_u, local_version, remote_version))
                if local_version is None or remote_version is None or local_version < remote_version:
                    self._log("%r added to download queue" % (relpath_u,))
                    self._append_to_batch(relpath_u, file_node, metadata)
        d.addCallback(scan_listing)
        d.addBoth(self._logcb, "end of _scan_remote")
        return d

    def _scan_remote_collective(self):
        self._log("_scan_remote_collective")
        self._download_scan_batch = {} # XXX

        if self._collective_dirnode is None:
            return
        collective_dirmap_d = self._collective_dirnode.list()
        def do_list(result):
            others = [x for x in result.keys()]
            return result, others
        collective_dirmap_d.addCallback(do_list)
        def scan_collective(result):
            d = defer.succeed(None)
            collective_dirmap, others_list = result
            for dir_name in others_list:
                d.addCallback(lambda x, dir_name=dir_name: self._scan_remote(dir_name, collective_dirmap[dir_name][0]))
                # XXX todo add errback
            return d
        collective_dirmap_d.addCallback(scan_collective)
        collective_dirmap_d.addCallback(self._filter_scan_batch)
        collective_dirmap_d.addCallback(self._add_batch_to_download_queue)
        return collective_dirmap_d

    def _add_batch_to_download_queue(self, result):
        self._log("result = %r" % (result,))
        self._log("deque = %r" % (self._deque,))
        self._deque.extend(result)
        self._log("deque after = %r" % (self._deque,))
        self._count('objects_queued', len(result))
        self._log("pending = %r" % (self._pending,))
        self._pending.update(map(lambda x: x[0], result))
        self._log("pending after = %r" % (self._pending,))

    def _filter_scan_batch(self, result):
        self._log("_filter_scan_batch")
        extension = [] # consider whether this should be a dict
        for relpath_u in self._download_scan_batch.keys():
            if relpath_u in self._pending:
                continue
            file_node, metadata = max(self._download_scan_batch[relpath_u], key=lambda x: x[1]['version'])
            if self._should_download(relpath_u, metadata['version']):
                extension += [(relpath_u, file_node, metadata)]
            else:
                self._log("Excluding %r" % (relpath_u,))
                self._count('objects_excluded')
                self._call_hook(None, 'processed')
        return extension

    def _when_queue_is_empty(self):
        d = task.deferLater(self._clock, self._turn_delay, self._scan_remote_collective)
        d.addBoth(self._logcb, "after _scan_remote_collective")
        d.addCallback(lambda ign: self._turn_deque())
        return d

    def _process(self, item, now=None):
        self._log("_process(%r)" % (item,))
        if now is None:
            now = time.time()
        (relpath_u, file_node, metadata) = item
        fp = self._get_filepath(relpath_u)
        abspath_u = unicode_from_filepath(fp)
        conflict_path_u = self._get_conflicted_filename(abspath_u)
        d = defer.succeed(None)

        def do_update_db(written_abspath_u):
            filecap = file_node.get_uri()
            last_uploaded_uri = metadata.get('last_uploaded_uri', None)
            last_downloaded_uri = filecap
            last_downloaded_timestamp = now
            written_pathinfo = get_pathinfo(written_abspath_u)

            if not written_pathinfo.exists and not metadata.get('deleted', False):
                raise Exception("downloaded object %s disappeared" % quote_local_unicode_path(written_abspath_u))

            self._db.did_upload_version(relpath_u, metadata['version'], last_uploaded_uri,
                                        last_downloaded_uri, last_downloaded_timestamp, written_pathinfo)
            self._count('objects_downloaded')
        def failed(f):
            self._log("download failed: %s" % (str(f),))
            self._count('objects_failed')
            return f

        if os.path.isfile(conflict_path_u):
            def fail(res):
                raise ConflictError("download failed: already conflicted: %r" % (relpath_u,))
            d.addCallback(fail)
        else:
            is_conflict = False
            if self._db.check_file_db_exists(relpath_u):
                dmd_last_downloaded_uri = metadata.get('last_downloaded_uri', None)
                local_last_downloaded_uri = self._db.get_last_downloaded_uri(relpath_u)
                print "metadata %r" % (metadata,)
                print "<<<<--- if %r != %r" % (dmd_last_downloaded_uri, local_last_downloaded_uri)
                if dmd_last_downloaded_uri is not None and local_last_downloaded_uri is not None:
                    if dmd_last_downloaded_uri != local_last_downloaded_uri:
                        is_conflict = True
                        self._count('objects_conflicted')

                #dmd_last_uploaded_uri = metadata.get('last_uploaded_uri', None)
                #local_last_uploaded_uri = ...

            if relpath_u.endswith(u"/"):
                if metadata.get('deleted', False):
                    self._log("rmdir(%r) ignored" % (abspath_u,))
                else:
                    self._log("mkdir(%r)" % (abspath_u,))
                    d.addCallback(lambda ign: fileutil.make_dirs(abspath_u))
                    d.addCallback(lambda ign: abspath_u)
            else:
                if metadata.get('deleted', False):
                    d.addCallback(lambda ign: self._rename_deleted_file(abspath_u))
                else:
                    d.addCallback(lambda ign: file_node.download_best_version())
                    d.addCallback(lambda contents: self._write_downloaded_file(abspath_u, contents,
                                                                               is_conflict=is_conflict))

        d.addCallbacks(do_update_db, failed)

        def remove_from_pending(res):
            self._pending.remove(relpath_u)
            return res
        d.addBoth(remove_from_pending)
        def trap_conflicts(f):
            f.trap(ConflictError)
            return None
        d.addErrback(trap_conflicts)
        return d
