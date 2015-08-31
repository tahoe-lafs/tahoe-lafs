
import sys, os, stat
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
from allmydata.util.fileutil import precondition_abspath, get_pathinfo, abspath_expanduser_unicode
from allmydata.util.assertutil import precondition
from allmydata.util.deferredutil import HookMixin
from allmydata.util.encodingutil import listdir_unicode, to_filepath, \
     unicode_from_filepath, quote_local_unicode_path, FilenameEncodingError
from allmydata.immutable.upload import FileName, Data
from allmydata import backupdb, magicpath


IN_EXCL_UNLINK = 0x04000000L

def get_inotify_module():
    try:
        if sys.platform == "win32":
            from allmydata.windows import inotify
        elif runtime.platform.supportsINotify():
            from twisted.internet import inotify
        else:
            raise NotImplementedError("filesystem notification needed for drop-upload is not supported.\n"
                                      "This currently requires Linux or Windows.")
        return inotify
    except (ImportError, AttributeError) as e:
        log.msg(e)
        if sys.platform == "win32":
            raise NotImplementedError("filesystem notification needed for drop-upload is not supported.\n"
                                      "Windows support requires at least Vista, and has only been tested on Windows 7.")
        raise


class MagicFolder(service.MultiService):
    name = 'magic-folder'

    def __init__(self, client, upload_dircap, collective_dircap, local_path_u, dbfile, inotify=None,
                 pending_delay=1.0):
        precondition_abspath(local_path_u)

        service.MultiService.__init__(self)

        db = backupdb.get_backupdb(dbfile, create_version=(backupdb.SCHEMA_v3, 3))
        if db is None:
            return Failure(Exception('ERROR: Unable to load magic folder db.'))

        # for tests
        self._client = client
        self._db = db

        self.is_ready = False

        self.uploader = Uploader(client, local_path_u, db, upload_dircap, inotify, pending_delay)
        self.downloader = Downloader(client, local_path_u, db, collective_dircap)

    def startService(self):
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
        #print "finish"
        d = self.uploader.stop()
        d2 = self.downloader.stop()
        d.addCallback(lambda ign: d2)
        return d

    def remove_service(self):
        return service.MultiService.disownServiceParent(self)


class QueueMixin(HookMixin):
    def __init__(self, client, local_path_u, db, name):
        self._client = client
        self._local_path_u = local_path_u
        self._local_path = to_filepath(local_path_u)
        self._db = db
        self._name = name
        self._hooks = {'processed': None}

        if not self._local_path.exists():
            raise AssertionError("The '[magic_folder] local.directory' parameter was %s "
                                 "but there is no directory at that location."
                                 % quote_local_unicode_path(self._local_path_u))
        if not self._local_path.isdir():
            raise AssertionError("The '[magic_folder] local.directory' parameter was %s "
                                 "but the thing at that location is not a directory."
                                 % quote_local_unicode_path(self._local_path_u))

        self._deque = deque()
        self._lazy_tail = defer.succeed(None)
        self._pending = set()
        self._callback = lambda ign: None
        self._ignore_count = 0
        self._stopped = False
        self._turn_delay = 0

    def _count(self, counter_name, delta=1):
        self._client.stats_provider.count('magic_folder.%s.%s' % (self._name, counter_name), delta)

    def _log(self, msg):
        s = "Magic Folder %s: %s" % (self._name, msg)
        self._client.log(s)
        #print s
        #open("events", "ab+").write(msg)

    def _append_to_deque(self, path):
        if path in self._pending:
            return
        self._deque.append(path)
        self._pending.add(path)
        self._count('objects_queued')
        if self.is_ready:
            reactor.callLater(0, self._turn_deque)

    def _turn_deque(self):
        if self._stopped:
            return
        try:
            item = self._deque.pop()
        except IndexError:
            self._log("deque is now empty")
            self._lazy_tail.addCallback(lambda ign: self._when_queue_is_empty())
        else:
            self._lazy_tail.addCallback(lambda ign: self._process(item))
            self._lazy_tail.addBoth(self._call_hook, 'processed')
            self._lazy_tail.addErrback(log.err)
            self._lazy_tail.addCallback(lambda ign: task.deferLater(reactor, self._turn_delay, self._turn_deque))


class Uploader(QueueMixin):
    def __init__(self, client, local_path_u, db, upload_dircap, inotify, pending_delay):
        QueueMixin.__init__(self, client, local_path_u, db, 'uploader')

        self.is_ready = False

        # TODO: allow a path rather than a cap URI.
        self._upload_dirnode = self._client.create_node_from_uri(upload_dircap)
        if not IDirectoryNode.providedBy(self._upload_dirnode):
            raise AssertionError("The URI in 'private/magic_folder_dircap' does not refer to a directory.")
        if self._upload_dirnode.is_unknown() or self._upload_dirnode.is_readonly():
            raise AssertionError("The URI in 'private/magic_folder_dircap' is not a writecap to a directory.")

        self._inotify = inotify or get_inotify_module()
        self._notifier = self._inotify.INotify()

        if hasattr(self._notifier, 'set_pending_delay'):
            self._notifier.set_pending_delay(pending_delay)

        # We don't watch for IN_CREATE, because that would cause us to read and upload a
        # possibly-incomplete file before the application has closed it. There should always
        # be an IN_CLOSE_WRITE after an IN_CREATE (I think).
        # TODO: what about IN_MOVE_SELF, IN_MOVED_FROM, or IN_UNMOUNT?
        #
        self.mask = ( self._inotify.IN_CLOSE_WRITE
                    | self._inotify.IN_MOVED_TO
                    | self._inotify.IN_MOVED_FROM
                    | self._inotify.IN_DELETE
                    | self._inotify.IN_ONLYDIR
                    | IN_EXCL_UNLINK
                    )
        self._notifier.watch(self._local_path, mask=self.mask, callbacks=[self._notify],
                             recursive=True)

    def start_monitoring(self):
        d = self._notifier.startReading()
        self._count('dirs_monitored')
        return d

    def stop(self):
        self._notifier.stopReading()
        self._count('dirs_monitored', -1)
        if hasattr(self._notifier, 'wait_until_stopped'):
            d = self._notifier.wait_until_stopped()
        else:
            d = defer.succeed(None)
        d.addCallback(lambda ign: self._lazy_tail)
        return d

    def start_scanning(self):
        self.is_ready = True
        d = self._scan(self._local_path_u) # XXX do not want dropped deferreds!
        self._turn_deque()
        return d

    def _scan(self, local_path_u):  # XXX should this take a FilePath?
        if not os.path.isdir(local_path_u):
            raise AssertionError("Programmer error: _scan() must be passed a directory path.")
        quoted_path = quote_local_unicode_path(local_path_u)
        try:
            children = listdir_unicode(local_path_u)
        except EnvironmentError:
            raise(Exception("WARNING: magic folder: permission denied on directory %s" % (quoted_path,)))
        except FilenameEncodingError:
            raise(Exception("WARNING: magic folder: could not list directory %s due to a filename encoding error" % (quoted_path,)))

        d = defer.succeed(None)
        for child in children:
            assert isinstance(child, unicode), child
            d.addCallback(lambda ign, child=child: os.path.join(local_path_u, child))
            d.addCallback(self._process_child)
            d.addErrback(log.err)

        return d

    def _notify(self, opaque, path, events_mask):
        self._log("inotify event %r, %r, %r\n" % (opaque, path, ', '.join(self._inotify.humanReadableMask(events_mask))))
        path_u = unicode_from_filepath(path)
        self._append_to_deque(path_u)

    def _when_queue_is_empty(self):
        return defer.succeed(None)

    def _process_child(self, path_u):
        precondition(isinstance(path_u, unicode), path_u)

        pathinfo = get_pathinfo(path_u)

        if pathinfo.islink:
            self.warn("WARNING: cannot backup symlink %s" % quote_local_unicode_path(path_u))
            return None
        elif pathinfo.isdir:
            # process directories unconditionally
            self._append_to_deque(path_u)

            # recurse on the child directory
            return self._scan(path_u)
        elif pathinfo.isfile:
            file_version = self._db.get_local_file_version(path_u)
            if file_version is None:
                # XXX upload if we didn't record our version in magicfolder db?
                self._append_to_deque(path_u)
                return None
            else:
                d2 = self._get_collective_latest_file(path_u)
                def _got_latest_file((file_node, metadata)):
                    collective_version = metadata['version']
                    if collective_version is None:
                        return None
                    if file_version > collective_version:
                        self._append_to_upload_deque(path_u)
                    elif file_version < collective_version: # FIXME Daira thinks this is wrong
                        # if a collective version of the file is newer than ours
                        # we must download it and unlink the old file from our upload dirnode
                        self._append_to_download_deque(path_u)
                        # XXX where should we save the returned deferred?
                        return self._upload_dirnode.delete(path_u, must_be_file=True)
                    else:
                        # XXX same version. do nothing.
                        pass
                d2.addCallback(_got_latest_file)
                return d2
        else:
            self.warn("WARNING: cannot backup special file %s" % quote_local_unicode_path(path_u))
            return None

    def _process(self, path_u):
        precondition(isinstance(path_u, unicode), path_u)

        d = defer.succeed(None)

        def _maybe_upload(val):
            pathinfo = get_pathinfo(path_u)

            self._pending.remove(path_u)  # FIXME make _upload_pending hold relative paths
            relpath_u = os.path.relpath(path_u, self._local_path_u)
            encoded_name_u = magicpath.path2magic(relpath_u)

            if not pathinfo.exists:
                self._log("drop-upload: notified object %r disappeared "
                          "(this is normal for temporary objects)" % (path_u,))
                self._count('objects_disappeared')
                d2 = defer.succeed(None)
                if self._db.check_file_db_exists(relpath_u):
                    d2.addCallback(lambda ign: self._get_metadata(encoded_name_u))
                    current_version = self._db.get_local_file_version(relpath_u) + 1
                    def set_deleted(metadata):
                        metadata['version'] = current_version
                        metadata['deleted'] = True
                        empty_uploadable = Data("", self._client.convergence)
                        return self._upload_dirnode.add_file(encoded_name_u, empty_uploadable, overwrite=True, metadata=metadata)
                    d2.addCallback(set_deleted)
                    def add_db_entry(filenode):
                        filecap = filenode.get_uri()
                        size = 0
                        now = time.time()
                        ctime = now
                        mtime = now
                        self._db.did_upload_file(filecap, relpath_u, current_version, int(mtime), int(ctime), size)
                        self._count('files_uploaded')
                    d2.addCallback(lambda x: self._get_filenode(encoded_name_u))
                    d2.addCallback(add_db_entry)

                d2.addCallback(lambda x: Exception("file does not exist"))  # FIXME wrong
                return d2
            elif pathinfo.islink:
                self.warn("WARNING: cannot upload symlink %s" % quote_local_unicode_path(path_u))
                return None
            elif pathinfo.isdir:
                self._notifier.watch(to_filepath(path_u), mask=self.mask, callbacks=[self._notify], recursive=True)
                uploadable = Data("", self._client.convergence)
                encoded_name_u += u"@_"
                upload_d = self._upload_dirnode.add_file(encoded_name_u, uploadable, metadata={"version":0}, overwrite=True)
                def _succeeded(ign):
                    self._log("created subdirectory %r" % (path_u,))
                    self._count('directories_created')
                def _failed(f):
                    self._log("failed to create subdirectory %r" % (path_u,))
                    return f
                upload_d.addCallbacks(_succeeded, _failed)
                upload_d.addCallback(lambda ign: self._scan(path_u))
                return upload_d
            elif pathinfo.isfile:
                version = self._db.get_local_file_version(relpath_u)
                if version is None:
                    version = 0
                else:
                    version += 1

                uploadable = FileName(path_u, self._client.convergence)
                d2 = self._upload_dirnode.add_file(encoded_name_u, uploadable, metadata={"version":version}, overwrite=True)
                def add_db_entry(filenode):
                    filecap = filenode.get_uri()
                    # XXX maybe just pass pathinfo
                    self._db.did_upload_file(filecap, relpath_u, version,
                                             pathinfo.mtime, pathinfo.ctime, pathinfo.size)
                    self._count('files_uploaded')
                d2.addCallback(add_db_entry)
                return d2
            else:
                self.warn("WARNING: cannot process special file %s" % quote_local_unicode_path(path_u))
                return None

        d.addCallback(_maybe_upload)

        def _succeeded(res):
            self._count('objects_queued', -1)
            self._count('objects_succeeded')
            return res
        def _failed(f):
            self._count('objects_queued', -1)
            self._count('objects_failed')
            self._log("%r while processing %r" % (f, path_u))
            return f
        d.addCallbacks(_succeeded, _failed)
        return d

    def _get_metadata(self, encoded_name_u):
        try:
            d = self._upload_dirnode.get_metadata_for(encoded_name_u)
        except KeyError:
            return Failure()
        return d

    def _get_filenode(self, encoded_name_u):
        try:
            d = self._upload_dirnode.get(encoded_name_u)
        except KeyError:
            return Failure()
        return d


class Downloader(QueueMixin):
    def __init__(self, client, local_path_u, db, collective_dircap):
        QueueMixin.__init__(self, client, local_path_u, db, 'downloader')

        # TODO: allow a path rather than a cap URI.
        self._collective_dirnode = self._client.create_node_from_uri(collective_dircap)

        if not IDirectoryNode.providedBy(self._collective_dirnode):
            raise AssertionError("The URI in 'private/collective_dircap' does not refer to a directory.")
        if self._collective_dirnode.is_unknown() or not self._collective_dirnode.is_readonly():
            raise AssertionError("The URI in 'private/collective_dircap' is not a readonly cap to a directory.")

        self._turn_delay = 3 # delay between remote scans
        self._download_scan_batch = {} # path -> [(filenode, metadata)]

    def start_scanning(self):
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
        v = self._db.get_local_file_version(relpath_u)
        return (v is None or v < remote_version)

    def _get_local_latest(self, path_u):
        """_get_local_latest takes a unicode path string checks to see if this file object
        exists in our magic-folder db; if not then return None
        else check for an entry in our magic-folder db and return the version number.
        """
        if not os.path.exists(path_u):
            return None
        return self._db.get_local_file_version(path_u)

    def _get_collective_latest_file(self, filename):
        """_get_collective_latest_file takes a file path pointing to a file managed by
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
        print "------------------_scan_remote----------------------"
        d = dirnode.list()
        def scan_listing(listing_map):
            for name in listing_map.keys():
                file_node, metadata = listing_map[name]
                local_version = self._get_local_latest(name)
                remote_version = metadata.get('version', None)
                print "%r has local version %r, remote version %r" % (name, local_version, remote_version)
                if local_version is None or remote_version is None or local_version < remote_version:
                    print "added to download queue\n"
                    self._append_to_batch(name, file_node, metadata)
        d.addCallback(scan_listing)
        return d

    def _scan_remote_collective(self):
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
        self._deque.extend(result)
        self._pending.update(map(lambda x: x[0], result))

    def _filter_scan_batch(self, result):
        extension = [] # consider whether this should be a dict
        for name in self._download_scan_batch.keys():
            if name in self._pending:
                continue
            file_node, metadata = max(self._download_scan_batch[name], key=lambda x: x[1]['version'])
            if self._should_download(name, metadata['version']):
                extension += [(name, file_node, metadata)]
        return extension

    def _when_queue_is_empty(self):
        d = task.deferLater(reactor, self._turn_delay, self._scan_remote_collective)
        d.addCallback(lambda ign: self._turn_deque())
        return d

    def _process(self, item):
        (name, file_node, metadata) = item
        d = file_node.download_best_version()
        def succeeded(res):
            def do_update_db(result):
                filecap = file_node.get_uri()
                s = os.stat(name)
                size = s[stat.ST_SIZE]
                ctime = s[stat.ST_CTIME]
                mtime = s[stat.ST_MTIME]
                self._db.did_upload_file(filecap, name, metadata['version'], mtime, ctime, size)
            d2 = defer.succeed(res)
            absname = abspath_expanduser_unicode(name)
            d2.addCallback(lambda result: self._write_downloaded_file(absname, result))
            d2.addCallback(do_update_db)
            self._count('objects_downloaded')
            return d2
        def failed(f):
            self._log("download failed: %s" % (str(f),))
            self._count('objects_download_failed')
            return f
        d.addCallbacks(succeeded, failed)
        def remove_from_pending(res):
            self._pending.remove(name)
            return res
        d.addBoth(remove_from_pending)
        return d

    FUDGE_SECONDS = 10.0

    @classmethod
    def _write_downloaded_file(cls, path, file_contents, is_conflict=False, now=None):
        # 1. Write a temporary file, say .foo.tmp.
        # 2. is_conflict determines whether this is an overwrite or a conflict.
        # 3. Set the mtime of the replacement file to be T seconds before the
        #    current local time.
        # 4. Perform a file replacement with backup filename foo.backup,
        #    replaced file foo, and replacement file .foo.tmp. If any step of
        #    this operation fails, reclassify as a conflict and stop.
        #
        # Returns the path of the destination file.

        precondition(isinstance(path, unicode), path=path)

        replacement_path = path + u".tmp"  # FIXME more unique
        backup_path = path + u".backup"
        if now is None:
            now = time.time()

        fileutil.write(replacement_path, file_contents)
        os.utime(replacement_path, (now, now - cls.FUDGE_SECONDS))
        if is_conflict:
            return cls._rename_conflicted_file(path, replacement_path)
        else:
            try:
                fileutil.replace_file(path, replacement_path, backup_path)
                return path
            except fileutil.ConflictError:
                return cls._rename_conflicted_file(path, replacement_path)

    @classmethod
    def _rename_conflicted_file(self, path, replacement_path):
        conflict_path = path + u".conflict"
        fileutil.rename_no_overwrite(replacement_path, conflict_path)
        return conflict_path
