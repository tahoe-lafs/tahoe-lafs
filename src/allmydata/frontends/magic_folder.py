
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
        self._local_dir = abspath_expanduser_unicode(local_dir)
        self._upload_lazy_tail = defer.succeed(None)
        self._pending = set()
        self._client = client
        self._stats_provider = client.stats_provider
        self._convergence = client.convergence
        self._local_path = to_filepath(self._local_dir)
        self._dbfile = dbfile

        self._upload_deque = deque()
        self.is_upload_ready = False

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
        if not IDirectoryNode.providedBy(self._upload_dirnode):
            raise AssertionError("The URI in 'private/magic_folder_dircap' does not refer to a directory.")
        if self._upload_dirnode.is_unknown() or self._upload_dirnode.is_readonly():
            raise AssertionError("The URI in 'private/magic_folder_dircap' is not a writecap to a directory.")

        self._processed_callback = lambda ign: None
        self._ignore_count = 0

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

    def _db_file_is_uploaded(self, childpath):
        """_db_file_is_uploaded returns true if the file was previously uploaded
        """ 
        assert self._db != None
        r = self._db.check_file(childpath)
        filecap = r.was_uploaded()
        if filecap is False:
            return True

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
                self._append_to_deque(childpath)

                # recurse on the child directory
                self._scan(childpath)
            elif isfile:
                is_uploaded = self._db_file_is_uploaded(childpath)
                if not is_uploaded: 
                    self._append_to_deque(childpath)
            else:
                self.warn("WARNING: cannot backup special file %s" % quote_local_unicode_path(childpath))

    def startService(self):
        self._db = backupdb.get_backupdb(self._dbfile)
        if self._db is None:
            return Failure(Exception('ERROR: Unable to load magic folder db.'))

        service.MultiService.startService(self)
        d = self._notifier.startReading()

        self._scan(self._local_dir)

        self._stats_provider.count('magic_folder.dirs_monitored', 1)
        return d

    def upload_ready(self):
        """upload_ready is used to signal us to start
        processing the upload items...
        """
        self.is_upload_ready = True
        self._turn_deque()

    def _append_to_deque(self, path):
        self._upload_deque.append(path)
        self._pending.add(path)
        self._stats_provider.count('magic_folder.objects_queued', 1)
        if self.is_upload_ready:
            reactor.callLater(0, self._turn_deque)

    def _turn_deque(self):
        try:
            path = self._upload_deque.pop()
        except IndexError:
            self._log("magic folder upload deque is now empty")
            self._upload_lazy_tail = defer.succeed(None)
            return
        self._upload_lazy_tail.addCallback(lambda ign: task.deferLater(reactor, 0, self._process, path))
        self._upload_lazy_tail.addCallback(lambda ign: self._turn_deque())

    def _notify(self, opaque, path, events_mask):
        self._log("inotify event %r, %r, %r\n" % (opaque, path, ', '.join(self._inotify.humanReadableMask(events_mask))))
        path_u = unicode_from_filepath(path)
        if path_u not in self._pending:
            self._append_to_deque(path_u)

    def _process(self, path):
        d = defer.succeed(None)

        def _add_file(name):
            u = FileName(path, self._convergence)
            return self._upload_dirnode.add_file(name, u, overwrite=True)

        def _add_dir(name):
            self._notifier.watch(to_filepath(path), mask=self.mask, callbacks=[self._notify], recursive=True)
            u = Data("", self._convergence)
            name += "@_"
            d2 = self._upload_dirnode.add_file(name, u, overwrite=True)
            def _succeeded(ign):
                self._log("created subdirectory %r" % (path,))
                self._stats_provider.count('magic_folder.directories_created', 1)
            def _failed(f):
                self._log("failed to create subdirectory %r" % (path,))
                return f
            d2.addCallbacks(_succeeded, _failed)
            d2.addCallback(lambda ign: self._scan(path))
            return d2

        def _maybe_upload(val):
            self._pending.remove(path)
            relpath = os.path.relpath(path, self._local_dir)
            name = magicpath.path2magic(relpath)

            if not os.path.exists(path):
                self._log("drop-upload: notified object %r disappeared "
                          "(this is normal for temporary objects)" % (path,))
                self._stats_provider.count('magic_folder.objects_disappeared', 1)

                # XXX todo: check if file exists in magic folder db
                # ...
                if not self._db_file_is_uploaded(path):
                    return NoSuchChildError("not uploading non-existent file")
                else:
                    # XXX ...
                    u = Data("", self._convergence)
                    d2 = self._parent.add_file(name, u, overwrite=True)
                    def get_metadata(d):
                        return self._parent.get_metadata_for(name)
                    def set_deleted(metadata):
                        metadata['version'] += 1
                        metadata['deleted'] = True
                        return self._parent.set_metadata_for(name, metadata)
                    d2.addCallback(get_metadata)
                    d2.addCallback(set_deleted)
                    return NoSuchChildError("not uploading non-existent file")
            elif os.path.islink(path):
                raise Exception("symlink not being processed")
            if os.path.isdir(path):
                return _add_dir(name)
            elif os.path.isfile(path):
                d2 = _add_file(name)
                def add_db_entry(filenode):
                    filecap = filenode.get_uri()
                    s = os.stat(path)
                    size = s[stat.ST_SIZE]
                    ctime = s[stat.ST_CTIME]
                    mtime = s[stat.ST_MTIME]
                    self._db.did_upload_file(filecap, path, mtime, ctime, size)
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

    def _do_processed_callback(self, res):
        if self._ignore_count == 0:
            self._processed_callback(res)
        else:
            self._ignore_count -= 1
        return None  # intentionally suppress failures, which have already been logged

    def set_processed_callback(self, callback, ignore_count=0):
        """
        This sets a function that will be called after a notification has been processed
        (successfully or unsuccessfully).
        """
        self._processed_callback = callback
        self._ignore_count = ignore_count

    def finish(self, for_tests=False):
        self._notifier.stopReading()
        self._stats_provider.count('magic_folder.dirs_monitored', -1)
        if for_tests and hasattr(self._notifier, 'wait_until_stopped'):
            return self._notifier.wait_until_stopped()
        else:
            return defer.succeed(None)

    def remove_service(self):
        return service.MultiService.disownServiceParent(self)

    def _log(self, msg):
        self._client.log("drop-upload: " + msg)
        #open("events", "ab+").write(msg)
