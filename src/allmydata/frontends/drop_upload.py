
import sys

from twisted.internet import defer
from twisted.python.filepath import FilePath
from twisted.application import service
from foolscap.api import eventually

from allmydata.interfaces import IDirectoryNode

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
        self._client = client
        self._stats_provider = client.stats_provider
        self._convergence = client.convergence
        self._local_path = to_filepath(self._local_dir)
        self._dbfile = dbfile

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

        self._notifier = inotify.INotify()
        if hasattr(self._notifier, 'set_pending_delay'):
            self._notifier.set_pending_delay(pending_delay)

        # We don't watch for IN_CREATE, because that would cause us to read and upload a
        # possibly-incomplete file before the application has closed it. There should always
        # be an IN_CLOSE_WRITE after an IN_CREATE (I think).
        # TODO: what about IN_MOVE_SELF or IN_UNMOUNT?
        mask = inotify.IN_CLOSE_WRITE | inotify.IN_MOVED_TO | inotify.IN_ONLYDIR
        self._notifier.watch(self._local_path, mask=mask, callbacks=[self._notify])

    def _check_db_file(self, childpath):
        # returns True if the file must be uploaded.
        assert self._db != None
        r = self._db.check_file(childpath)
        filecap = r.was_uploaded()
        if filecap is False:
            return True

    def startService(self):
        self._db = backupdb.get_backupdb(self._dbfile)
        if self._db is None:
            return Failure(Exception('ERROR: Unable to load magic folder db.'))

        service.MultiService.startService(self)
        d = self._notifier.startReading()
        self._stats_provider.count('drop_upload.dirs_monitored', 1)
        return d

    def upload_ready(self):
        """upload_ready is used to signal us to start
        processing the upload items...
        """
        self.is_upload_ready = True

    def _notify(self, opaque, path, events_mask):
        self._log("inotify event %r, %r, %r\n" % (opaque, path, ', '.join(self._inotify.humanReadableMask(events_mask))))

        self._stats_provider.count('drop_upload.files_queued', 1)
        eventually(self._process, opaque, path, events_mask)

    def _process(self, opaque, path, events_mask):
        d = defer.succeed(None)

        # FIXME: if this already exists as a mutable file, we replace the directory entry,
        # but we should probably modify the file (as the SFTP frontend does).
        def _add_file(ign):
            name = path.basename()
            # on Windows the name is already Unicode
            if not isinstance(name, unicode):
                name = name.decode(get_filesystem_encoding())

            u = FileName(path.path, self._convergence)
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
