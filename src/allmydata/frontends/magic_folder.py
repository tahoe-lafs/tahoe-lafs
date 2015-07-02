
import sys, os, stat
import os.path
from collections import deque

from twisted.internet import defer, reactor, task
from twisted.python.failure import Failure
from twisted.python import runtime
from twisted.application import service

from allmydata.interfaces import IDirectoryNode
from allmydata.util import log
from allmydata.util.fileutil import abspath_expanduser_unicode, precondition_abspath
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

    def __init__(self, client, upload_dircap, collective_dircap, local_dir, dbfile, inotify=None,
                 pending_delay=1.0):
        precondition_abspath(local_dir)

        service.MultiService.__init__(self)
        self._stopped = False
        self._remote_scan_delay = 10 # XXX
        self._local_dir = abspath_expanduser_unicode(local_dir)
        self._upload_lazy_tail = defer.succeed(None)
        self._upload_pending = set()
        self._download_scan_batch = {}
        self._download_lazy_tail = defer.succeed(None)
        self._download_pending = set()
        self._client = client
        self._stats_provider = client.stats_provider
        self._convergence = client.convergence
        self._local_path = to_filepath(self._local_dir)
        self._dbfile = dbfile

        self._download_deque = deque()
        self._upload_deque = deque()
        self.is_ready = False

        self._inotify = inotify or get_inotify_module()

        if not self._local_path.exists():
            raise AssertionError("The '[magic_folder] local.directory' parameter was %s "
                                 "but there is no directory at that location."
                                 % quote_local_unicode_path(local_dir))
        if not self._local_path.isdir():
            raise AssertionError("The '[magic_folder] local.directory' parameter was %s "
                                 "but the thing at that location is not a directory."
                                 % quote_local_unicode_path(local_dir))

        # TODO: allow a path rather than a cap URI.
        self._upload_dirnode = self._client.create_node_from_uri(upload_dircap)
        if collective_dircap != "":
            # XXX this condition used for some unit tests
            self._collective_dirnode = self._client.create_node_from_uri(collective_dircap)
            if not IDirectoryNode.providedBy(self._collective_dirnode):
                raise AssertionError("The URI in 'private/collective_dircap' does not refer to a directory.")
            if self._collective_dirnode.is_unknown() or not self._collective_dirnode.is_readonly():
                raise AssertionError("The URI in 'private/collective_dircap' is not a readonly cap to a directory.")

        if not IDirectoryNode.providedBy(self._upload_dirnode):
            raise AssertionError("The URI in 'private/magic_folder_dircap' does not refer to a directory.")
        if self._upload_dirnode.is_unknown() or self._upload_dirnode.is_readonly():
            raise AssertionError("The URI in 'private/magic_folder_dircap' is not a writecap to a directory.")

        self._processed_callback = lambda ign: None
        self._download_callback = lambda ign: None
        self._ignore_count = 0
        self._download_ignore_count = 0

        self._notifier = inotify.INotify()
        if hasattr(self._notifier, 'set_pending_delay'):
            self._notifier.set_pending_delay(pending_delay)

        # We don't watch for IN_CREATE, because that would cause us to read and upload a
        # possibly-incomplete file before the application has closed it. There should always
        # be an IN_CLOSE_WRITE after an IN_CREATE (I think).
        # TODO: what about IN_MOVE_SELF, IN_MOVED_FROM, or IN_UNMOUNT?
        #
        self.mask = ( inotify.IN_CLOSE_WRITE
                    | inotify.IN_MOVED_TO
                    | inotify.IN_MOVED_FROM
                    | inotify.IN_DELETE
                    | inotify.IN_ONLYDIR
                    | IN_EXCL_UNLINK
                    )
        self._notifier.watch(self._local_path, mask=self.mask, callbacks=[self._notify],
                             recursive=True)


        self._scan_remote_collective()

    def _should_download(self, path, remote_version):
        """
        _should_download returns a bool indicating whether or not a remote object should be downloaded.
        We check the remote metadata version against our magic-folder db version number;
        latest version wins.
        """
        v = self._db.get_local_file_version(path)
        if v is None:
            return True
        else:
            if v < remote_version:
                return True
            else:
                return False

    def _scan_remote(self, nickname, dirnode):
        listing_d = dirnode.list()
        self._download_scan_batch = {}
        def scan_listing(listing_map):
            for name in listing_map.keys():
                file_node, metadata = listing_map[name]
                if self._download_scan_batch.has_key(name):
                    self._download_scan_batch[name] += [(name, file_node, metadata)]
                else:
                    self._download_scan_batch[name] = [(name, file_node, metadata)]
        listing_d.addCallback(scan_listing)
        return listing_d

    def _scan_remote_collective(self):
        upload_readonly_dircap = self._upload_dirnode.get_readonly_uri()
        collective_dirmap_d = self._collective_dirnode.list()
        def do_filter(result):
            def not_mine(x):
                return result[x][0].get_readonly_uri() != upload_readonly_dircap
            others = filter(not_mine, result.keys())
            return result, others
        collective_dirmap_d.addCallback(do_filter)
        def scan_collective(result):
            d = defer.succeed(None)
            collective_dirmap, others_list = result
            for dir_name in others_list:
                # XXX this is broken
                d.addCallback(lambda x: self._scan_remote(dir_name, collective_dirmap[dir_name][0]))
                collective_dirmap_d.addCallback(self._filter_scan_batch)
                collective_dirmap_d.addCallback(self._add_batch_to_download_queue)
            return d
        collective_dirmap_d.addCallback(scan_collective)
        return collective_dirmap_d

    def _add_batch_to_download_queue(self, result):
        self._download_deque.extend(result)
        self._download_pending.update(map(lambda x: x[0], result))

    def _filter_scan_batch(self, result):
        extension = []
        for name in self._download_scan_batch.keys():
            if name in self._download_pending:
                continue
            for item in self._download_scan_batch[name]:
                (nickname, file_node, metadata) = item
                if self._should_download(name, metadata['version']):
                    extension += [(name, file_node, metadata)]
        return extension

    def _download_file(self, name, file_node):
        d = file_node.download_best_version()
        def succeeded(res):
            d.addCallback(lambda result: self._write_downloaded_file(name, result))
            self._stats_provider.count('magic_folder.objects_downloaded', +1)
            return None
        def failed(f):
            return failure.Failure("download failed")
        def remove_from_pending(result):
            self._download_pending = self._download_pending.difference(set([name]))
        d.addCallbacks(succeeded, failed)
        d.addBoth(self._do_download_callback)
        d.addBoth(remove_from_pending)
        return d

    def _write_downloaded_file(self, name, file_contents):
        print "_write_downloaded_file: no-op."

    def _db_file_is_uploaded(self, childpath):
        """_db_file_is_uploaded returns true if the file was previously uploaded
        """ 
        assert self._db != None
        r = self._db.check_file(childpath)
        filecap = r.was_uploaded()
        if filecap is False:
            return False

    def _scan(self, localpath):
        if not os.path.isdir(localpath):
            raise AssertionError("Programmer error: _scan() must be passed a directory path.")
        quoted_path = quote_local_unicode_path(localpath)
        try:
            children = listdir_unicode(localpath)
        except EnvironmentError:
            raise(Exception("WARNING: magic folder: permission denied on directory %s" % (quoted_path,)))
        except FilenameEncodingError:
            raise(Exception("WARNING: magic folder: could not list directory %s due to a filename encoding error" % (quoted_path,)))

        for child in children:
            assert isinstance(child, unicode), child
            childpath = os.path.join(localpath, child)
            # note: symlinks to directories are both islink() and isdir()
            isdir = os.path.isdir(childpath)
            isfile = os.path.isfile(childpath)
            islink = os.path.islink(childpath)

            if islink:
                self.warn("WARNING: cannot backup symlink %s" % quote_local_unicode_path(childpath))
            elif isdir:
                # process directories unconditionally
                self._append_to_upload_deque(childpath)

                # recurse on the child directory
                self._scan(childpath)
            elif isfile:
                is_uploaded = self._db_file_is_uploaded(childpath)
                if not is_uploaded:
                    self._append_to_upload_deque(childpath)
            else:
                self.warn("WARNING: cannot backup special file %s" % quote_local_unicode_path(childpath))

    def startService(self):
        self._db = backupdb.get_backupdb(self._dbfile, create_version=(backupdb.SCHEMA_v3, 3))
        if self._db is None:
            return Failure(Exception('ERROR: Unable to load magic folder db.'))

        service.MultiService.startService(self)
        d = self._notifier.startReading()

        self._scan(self._local_dir)

        self._stats_provider.count('magic_folder.dirs_monitored', 1)
        return d

    def ready(self):
        """ready is used to signal us to start
        processing the upload and download items...
        """
        self.is_ready = True
        self._turn_upload_deque()
        self._turn_download_deque()

    def _turn_download_deque(self):
        if self._stopped:
            return
        try:
            file_path, file_node, metadata = self._download_deque.pop()
        except IndexError:
            self._log("magic folder upload deque is now empty")
            self._download_lazy_tail = defer.succeed(None)
            self._download_lazy_tail.addCallback(lambda ign: task.deferLater(reactor, self._remote_scan_delay, self._scan_remote_collective))
            self._download_lazy_tail.addCallback(lambda ign: task.deferLater(reactor, 0, self._turn_download_deque))
            return
        self._download_lazy_tail.addCallback(lambda ign: task.deferLater(reactor, 0, self._download_file, file_path, file_node))
        self._download_lazy_tail.addCallback(lambda ign: task.deferLater(reactor, self._remote_scan_delay, self._turn_download_deque))

    def _append_to_upload_deque(self, path):
        if path in self._upload_pending:
            return
        self._upload_deque.append(path)
        self._upload_pending.add(path)
        self._stats_provider.count('magic_folder.objects_queued', 1)
        if self.is_ready:
            reactor.callLater(0, self._turn_upload_deque)

    def _turn_upload_deque(self):
        try:
            path = self._upload_deque.pop()
        except IndexError:
            self._log("magic folder upload deque is now empty")
            self._upload_lazy_tail = defer.succeed(None)
            return
        self._upload_lazy_tail.addCallback(lambda ign: task.deferLater(reactor, 0, self._process, path))
        self._upload_lazy_tail.addCallback(lambda ign: self._turn_upload_deque())

    def _notify(self, opaque, path, events_mask):
        self._log("inotify event %r, %r, %r\n" % (opaque, path, ', '.join(self._inotify.humanReadableMask(events_mask))))
        path_u = unicode_from_filepath(path)
        self._append_to_upload_deque(path_u)

    def _process(self, path):
        d = defer.succeed(None)

        def _add_file(name, version):
            u = FileName(path, self._convergence)
            return self._upload_dirnode.add_file(name, u, metadata={"version":version}, overwrite=True)

        def _add_dir(name):
            self._notifier.watch(to_filepath(path), mask=self.mask, callbacks=[self._notify], recursive=True)
            u = Data("", self._convergence)
            name += "@_"
            upload_d = self._upload_dirnode.add_file(name, u, metadata={"version":1}, overwrite=True)
            def _succeeded(ign):
                self._log("created subdirectory %r" % (path,))
                self._stats_provider.count('magic_folder.directories_created', 1)
            def _failed(f):
                self._log("failed to create subdirectory %r" % (path,))
                return f
            upload_d.addCallbacks(_succeeded, _failed)
            upload_d.addCallback(lambda ign: self._scan(path))
            return upload_d

        def _maybe_upload(val):
            self._upload_pending.remove(path)
            relpath = os.path.relpath(path, self._local_dir)
            name = magicpath.path2magic(relpath)

            def get_metadata(result):
                try:
                    metadata_d = self._upload_dirnode.get_metadata_for(name)
                except KeyError:
                    return failure.Failure()
                return metadata_d

            if not os.path.exists(path):
                self._log("drop-upload: notified object %r disappeared "
                          "(this is normal for temporary objects)" % (path,))
                self._stats_provider.count('magic_folder.objects_disappeared', 1)
                d2 = defer.succeed(None)
                if self._db.check_file_db_exists(path):
                    d2.addCallback(get_metadata)
                    def set_deleted(metadata):
                        current_version = self._db.get_local_file_version(path) + 1
                        print "current version ", current_version
                        metadata['version'] = current_version
                        metadata['deleted'] = True
                        emptyUploadable = Data("", self._convergence)
                        return self._upload_dirnode.add_file(name, emptyUploadable, overwrite=True, metadata=metadata)
                    d2.addCallback(set_deleted)
                d2.addCallback(lambda x: Exception("file does not exist"))
                return d2
            elif os.path.islink(path):
                raise Exception("symlink not being processed")
            if os.path.isdir(path):
                return _add_dir(name)
            elif os.path.isfile(path):
                version = self._db.get_local_file_version(path)
                if version is None:
                    version = 1
                else:
                    version += 1
                d2 = _add_file(name, version)
                def add_db_entry(filenode):
                    filecap = filenode.get_uri()
                    s = os.stat(path)
                    size = s[stat.ST_SIZE]
                    ctime = s[stat.ST_CTIME]
                    mtime = s[stat.ST_MTIME]
                    self._db.did_upload_file(filecap, path, version, mtime, ctime, size)
                    self._stats_provider.count('magic_folder.files_uploaded', 1)
                d2.addCallback(add_db_entry)
                return d2
            else:
                raise Exception("non-directory/non-regular file not being processed")

        d.addCallback(_maybe_upload)

        def _succeeded(res):
            self._stats_provider.count('magic_folder.objects_queued', -1)
            self._stats_provider.count('magic_folder.objects_succeeded', 1)
            return res
        def _failed(f):
            self._stats_provider.count('magic_folder.objects_queued', -1)
            self._stats_provider.count('magic_folder.objects_failed', 1)
            self._log("%r while processing %r" % (f, path))
            return f
        d.addCallbacks(_succeeded, _failed)
        d.addBoth(self._do_processed_callback)
        return d

    def _do_download_callback(self, res):
        if self._download_ignore_count == 0:
            self._download_callback(res)
        else:
            self._download_ignore_count -= 1
        return None  # intentionally suppress failures, which have already been logged

    def _do_processed_callback(self, res):
        if self._ignore_count == 0:
            self._processed_callback(res)
        else:
            self._ignore_count -= 1
        return None  # intentionally suppress failures, which have already been logged

    def set_download_callback(self, callback, ignore_count=0):
        """
        set_download_callback sets a function that will be called after a
        remote filesystem notification has been processed (successfully or unsuccessfully).
        """
        self._download_callback = callback
        self._download_ignore_count = ignore_count

    def set_processed_callback(self, callback, ignore_count=0):
        """
        set_processed_callback sets a function that will be called after a
        local filesystem notification has been processed (successfully or unsuccessfully).
        """
        self._processed_callback = callback
        self._ignore_count = ignore_count

    def finish(self, for_tests=False):
        self._stopped = True
        self._notifier.stopReading()
        self._stats_provider.count('magic_folder.dirs_monitored', -1)

        if for_tests and hasattr(self._notifier, 'wait_until_stopped'):
            d = self._notifier.wait_until_stopped()
        else:
            d = defer.succeed(None)

        d.addCallback(lambda x: self._download_lazy_tail)
        return d

    def remove_service(self):
        return service.MultiService.disownServiceParent(self)

    def _log(self, msg):
        self._client.log("drop-upload: " + msg)
        #print "_log %s" % (msg,)
        #open("events", "ab+").write(msg)
