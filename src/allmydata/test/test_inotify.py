
from twisted.trial import unittest

from allmydata.frontends import magic_folder
from allmydata.frontends.magic_folder import MagicFolder, get_inotify_module, IN_EXCL_UNLINK


class TestInotify(unittest.TestCase, NonASCIIPathMixin):
    def setUp(self):
        GridTestMixin.setUp(self)
        self._local_filepath = abspath_expanduser_unicode(u"Alice-magic", base=self.basedir)
        self.mkdir_nonascii(self._local_filepath)
        self._inotify = get_inotify_module()
        self._notifier = self._inotify.INotify()
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

    def _notify(self, opaque, path, events_mask):
        if ((events_mask & self._inotify.IN_CREATE) != 0 and
            (events_mask & self._inotify.IN_ISDIR) == 0):
            self._log("ignoring event for %r (creation of non-directory)\n" % (path,))
            return

