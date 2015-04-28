
import sys, os, stat
from collections import deque

from twisted.internet import defer, reactor, task
from twisted.python.failure import Failure
from twisted.application import service

from allmydata.interfaces import IDirectoryNode, NoSuchChildError, ExistingChildError

from allmydata.util.fileutil import abspath_expanduser_unicode, precondition_abspath
from allmydata.util.encodingutil import listdir_unicode, to_filepath, \
     unicode_from_filepath, quote_local_unicode_path, FilenameEncodingError
from allmydata.immutable.upload import FileName
from allmydata.scripts import backupdb


class DropUploader(service.MultiService):
    name = 'drop-upload'

    def __init__(self, client, upload_dircap, local_dir, dbfile, inotify=None,
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

        if inotify is None:
            if sys.platform == "win32":
                from allmydata.windows import inotify
            else:
                from twisted.internet import inotify
        self._inotify = inotify

        if not self._local_path.exists():
            raise AssertionError("The '[drop_upload] local.directory' parameter was %s "
                                 "but there is no directory at that location."
                                 % quote_local_unicode_path(local_dir))
        if not self._local_path.isdir():
            raise AssertionError("The '[drop_upload] local.directory' parameter was %s "
                                 "but the thing at that location is not a directory."
                                 % quote_local_unicode_path(local_dir))

        # TODO: allow a path rather than a cap URI.
        self._parent = self._client.create_node_from_uri(upload_dircap)
        if not IDirectoryNode.providedBy(self._parent):
            raise AssertionError("The URI in 'private/drop_upload_dircap' does not refer to a directory.")
        if self._parent.is_unknown() or self._parent.is_readonly():
            raise AssertionError("The URI in 'private/drop_upload_dircap' is not a writecap to a directory.")

        self._uploaded_callback = lambda ign: None
        self._ignore_count = 0

        self._notifier = inotify.INotify()
        if hasattr(self._notifier, 'set_pending_delay'):
            self._notifier.set_pending_delay(pending_delay)

        # We don't watch for IN_CREATE, because that would cause us to read and upload a
        # possibly-incomplete file before the application has closed it. There should always
        # be an IN_CLOSE_WRITE after an IN_CREATE (I think).
        # TODO: what about IN_MOVE_SELF or IN_UNMOUNT?
        self.mask = inotify.IN_CLOSE_WRITE | inotify.IN_MOVED_TO | inotify.IN_ONLYDIR
        self._notifier.watch(self._local_path, mask=self.mask, callbacks=[self._notify],
                             recursive=True)

    def _check_db_file(self, childpath):
        # returns True if the file must be uploaded.
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
                must_upload = self._check_db_file(childpath)
                if must_upload:
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

        self._stats_provider.count('drop_upload.dirs_monitored', 1)
        return d

    def _add_to_dequeue(self, path):
        # XXX stub function. fix me later.
        #print "adding file to upload queue %s" % (path,)
        pass

    def Pause(self):
        self.is_upload_ready = False

    def Resume(self):
        self.is_upload_ready = True
        # XXX
        self._turn_deque()

    def upload_ready(self):
        """upload_ready is used to signal us to start
        processing the upload items...
        """
        self.is_upload_ready = True
        self._turn_deque()

    def _append_to_deque(self, path):
        self._upload_deque.append(path)
        self._pending.add(path)
        self._stats_provider.count('drop_upload.objects_queued', 1)
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

        # FIXME (ticket #1712): if this already exists as a mutable file, we replace the
        # directory entry, but we should probably modify the file (as the SFTP frontend does).
        def _add_file(ignore, name):
            u = FileName(path, self._convergence)
            return self._parent.add_file(name, u)

        def _add_dir(ignore, name):
            self._notifier.watch(to_filepath(path), mask=self.mask, callbacks=[self._notify], recursive=True)
            d2 = self._parent.create_subdirectory(name, overwrite=False)
            def _err(f):
                f.trap(ExistingChildError)
                self._log("subdirectory %r already exists" % (path,))
            d2.addCallbacks(lambda ign: self._log("created subdirectory %r" % (path,)), _err)
            def _failed(f):
                self._log("failed to create subdirectory %r due to %r" % (path, f))
                self._stats_provider.count('drop_upload.objects_failed', 1)
            d2.addCallback(_failed)
            d2.addCallback(lambda ign: self._scan(path))
            return d2

        def _maybe_upload(val):
            self._pending.remove(path)
            name = os.path.basename(path)

            if not os.path.exists(path):
                self._log("uploader: not uploading non-existent file.")
                self._stats_provider.count('drop_upload.objects_disappeared', 1)
                return NoSuchChildError("not uploading non-existent file")
            elif os.path.islink(path):
                self._log("operator ERROR: symlink not being processed.")
                return Failure()

            if os.path.isdir(path):
                d.addCallback(_add_dir, name)
                self._stats_provider.count('drop_upload.directories_created', 1)
                return None
            elif os.path.isfile(path):
                d.addCallback(_add_file, name)
                def add_db_entry(filenode):
                    filecap = filenode.get_uri()
                    s = os.stat(path)
                    size = s[stat.ST_SIZE]
                    ctime = s[stat.ST_CTIME]
                    mtime = s[stat.ST_MTIME]
                    self._db.did_upload_file(filecap, path, mtime, ctime, size)
                d.addCallback(add_db_entry)
                self._stats_provider.count('drop_upload.files_uploaded', 1)
                return None
            else:
                self._log("operator ERROR: non-directory/non-regular file not being processed.")
                return Failure()

        d.addCallback(_maybe_upload)

        def _succeeded(ign):
            self._stats_provider.count('drop_upload.objects_queued', -1)
            self._stats_provider.count('drop_upload.objects_uploaded', 1)

        def _failed(f):
            self._stats_provider.count('drop_upload.objects_queued', -1)
            if os.path.exists(path):
                self._log("drop-upload: %r failed to upload due to %r" % (path, f))
                self._stats_provider.count('drop_upload.objects_failed', 1)
                return f
            else:
                self._log("drop-upload: notified object %r disappeared "
                          "(this is normal for temporary objects): %r" % (path, f))
                return None

        d.addCallbacks(_succeeded, _failed)
        d.addBoth(self._do_upload_callback)
        return d

    def _do_upload_callback(self, res):
        if self._ignore_count == 0:
            self._uploaded_callback(res)
        else:
            self._ignore_count -= 1

    def set_uploaded_callback(self, callback, ignore_count=0):
        """
        This sets a function that will be called after a file has been uploaded.
        """
        self._uploaded_callback = callback
        self._ignore_count = ignore_count

    def finish(self, for_tests=False):
        self._notifier.stopReading()
        self._stats_provider.count('drop_upload.dirs_monitored', -1)
        if for_tests and hasattr(self._notifier, 'wait_until_stopped'):
            return self._notifier.wait_until_stopped()
        else:
            return defer.succeed(None)

    def remove_service(self):
        return service.MultiService.disownServiceParent(self)

    def _log(self, msg):
        self._client.log(msg)
        #open("events", "ab+").write(msg)
