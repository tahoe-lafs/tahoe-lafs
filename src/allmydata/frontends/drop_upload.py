
import sys, os
from collections import deque

from twisted.internet import defer
from twisted.python.failure import Failure
from twisted.python.filepath import FilePath
from twisted.application import service

from allmydata.interfaces import IDirectoryNode

from allmydata.util.encodingutil import quote_output, get_filesystem_encoding
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.immutable.upload import FileName
from allmydata.scripts import backupdb, tahoe_backup

from allmydata.util.encodingutil import listdir_unicode, quote_output, \
     quote_local_unicode_path, to_str, FilenameEncodingError, unicode_to_url



class DropUploader(service.MultiService):
    name = 'drop-upload'

    def __init__(self, client, upload_dircap, local_dir_utf8, dbfile, inotify=None, deque_max_len=100):
        service.MultiService.__init__(self)
        try:
            local_dir_u = abspath_expanduser_unicode(local_dir_utf8.decode('utf-8'))
            if sys.platform == "win32":
                local_dir = local_dir_u
            else:
                local_dir = local_dir_u.encode(get_filesystem_encoding())
        except (UnicodeEncodeError, UnicodeDecodeError):
            raise AssertionError("The '[drop_upload] local.directory' parameter %s was not valid UTF-8 or "
                                 "could not be represented in the filesystem encoding."
                                 % quote_output(local_dir_utf8))

        self._pending = set()
        self._client = client
        self._stats_provider = client.stats_provider
        self._convergence = client.convergence
        self._local_path = FilePath(local_dir)
        self._local_dir = unicode(local_dir, 'UTF-8')
        self._dbfile = dbfile

        self._upload_deque = deque(maxlen=deque_max_len)
        self.is_upload_ready = False

        if inotify is None:
            from twisted.internet import inotify
        self._inotify = inotify

        if not self._local_path.exists():
            raise AssertionError("The '[drop_upload] local.directory' parameter was %s but there is no directory at that location." % quote_output(local_dir_u))
        if not self._local_path.isdir():
            raise AssertionError("The '[drop_upload] local.directory' parameter was %s but the thing at that location is not a directory." % quote_output(local_dir_u))

        # TODO: allow a path rather than a cap URI.
        self._parent = self._client.create_node_from_uri(upload_dircap)
        if not IDirectoryNode.providedBy(self._parent):
            raise AssertionError("The '[drop_upload] upload.dircap' parameter does not refer to a directory.")
        if self._parent.is_unknown() or self._parent.is_readonly():
            raise AssertionError("The '[drop_upload] upload.dircap' parameter is not a writecap to a directory.")

        self._uploaded_callback = lambda ign: None

        self._notifier = inotify.INotify()

        # We don't watch for IN_CREATE, because that would cause us to read and upload a
        # possibly-incomplete file before the application has closed it. There should always
        # be an IN_CLOSE_WRITE after an IN_CREATE (I think).
        # TODO: what about IN_MOVE_SELF or IN_UNMOUNT?
        mask = inotify.IN_CLOSE_WRITE | inotify.IN_MOVED_TO | inotify.IN_ONLYDIR
        self._notifier.watch(self._local_path, mask=mask, callbacks=[self._notify])

    def _check_db_file(self, childpath):
        # returns True if the file must be uploaded.
        assert self._db != None
        use_timestamps = True
        r = self._db.check_file(childpath, use_timestamps)
        return not r.was_uploaded()

    def _scan(self, localpath):
        quoted_path = quote_local_unicode_path(localpath)
        try:
            children = listdir_unicode(localpath)
        except EnvironmentError:
            raise(Exception("WARNING: magic folder: permission denied on directory %s" % (quoted_path,)))
        except FilenameEncodingError:
            raise(Esception("WARNING: magic folder: could not list directory %s due to a filename encoding error" % (quoted_path,)))

        for child in children:
            assert isinstance(child, unicode), child
            childpath = os.path.join(localpath, child)
            # note: symlinks to directories are both islink() and isdir()
            if os.path.isdir(childpath) and not os.path.islink(childpath):
                # recurse on the child directory
                self._scan(childpath)
            elif os.path.isfile(childpath) and not os.path.islink(childpath):
                must_upload = self._check_db_file(childpath)
                if must_upload:
                    self._add_to_dequeue(childpath)
            else:
                if os.path.islink(childpath):
                    self.warn("WARNING: cannot backup symlink %s" % quote_local_unicode_path(childpath))
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

    def upload_ready(self):
        """upload_ready is used to signal us to start
        processing the upload items...
        """
        self.is_upload_ready = True
        self._process_deque()

    def _append_to_deque(self, func, path, event_mask):
        thunk = (func, path, event_mask)
        self._upload_deque.append(thunk)
        self._pending.add(path)
        if self.is_upload_ready:
            self._process_deque()

    def _process_deque(self):
        while True:
            try:
                fields = self._upload_deque.pop()
                func = fields[0]
                func(*fields[1:])
            except IndexError:
                break

    def _notify(self, opaque, path, events_mask):
        self._log("inotify event %r, %r, %r\n" % (opaque, path, ', '.join(self._inotify.humanReadableMask(events_mask))))
        self._stats_provider.count('drop_upload.files_queued', 1)
        if path not in self._pending:
            self._append_to_deque(self._process, path, events_mask)
            self._pending.add(path)

    def _process(self, path, events_mask):
        d = defer.succeed(None)

        # FIXME: if this already exists as a mutable file, we replace the directory entry,
        # but we should probably modify the file (as the SFTP frontend does).
        def _add_file(ign):
            name = path.basename()
            # on Windows the name is already Unicode
            if not isinstance(name, unicode):
                name = name.decode(get_filesystem_encoding())

            u = FileName(path.path, self._convergence)
            self._pending.remove(path)
            return self._parent.add_file(name, u)
        d.addCallback(_add_file)

        def _succeeded(ign):
            self._stats_provider.count('drop_upload.files_queued', -1)
            self._stats_provider.count('drop_upload.files_uploaded', 1)
        def _failed(f):
            self._stats_provider.count('drop_upload.files_queued', -1)
            if path.exists():
                self._log("drop-upload: %r failed to upload due to %r" % (path.path, f))
                self._stats_provider.count('drop_upload.files_failed', 1)
                return f
            else:
                self._log("drop-upload: notified file %r disappeared "
                          "(this is normal for temporary files): %r" % (path.path, f))
                self._stats_provider.count('drop_upload.files_disappeared', 1)
                return None
        d.addCallbacks(_succeeded, _failed)
        d.addBoth(self._uploaded_callback)
        return d

    def set_uploaded_callback(self, callback):
        """This sets a function that will be called after a file has been uploaded."""
        self._uploaded_callback = callback

    def finish(self, for_tests=False):
        self._notifier.stopReading()
        self._stats_provider.count('drop_upload.dirs_monitored', -1)
        if for_tests and hasattr(self._notifier, 'wait_until_stopped'):
            return self._notifier.wait_until_stopped()
        else:
            return defer.succeed(None)

    def _log(self, msg):
        self._client.log(msg)
        #open("events", "ab+").write(msg)
