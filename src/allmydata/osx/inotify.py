
import os, sys

from watchdog.observers import Observer  
from watchdog.events import FileSystemEventHandler  

from twisted.internet import reactor

from allmydata.util.pollmixin import PollMixin
from allmydata.util.assertutil import _assert, precondition
from allmydata.util import log, fileutil
from allmydata.util.fake_inotify import humanReadableMask, \
    IN_WATCH_MASK, IN_ACCESS, IN_MODIFY, IN_ATTRIB, IN_CLOSE_NOWRITE, IN_CLOSE_WRITE, \
    IN_OPEN, IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE, IN_DELETE_SELF, \
    IN_MOVE_SELF, IN_UNMOUNT, IN_Q_OVERFLOW, IN_IGNORED, IN_ONLYDIR, IN_DONT_FOLLOW, \
    IN_MASK_ADD, IN_ISDIR, IN_ONESHOT, IN_CLOSE, IN_MOVED, IN_CHANGED
[humanReadableMask, \
    IN_WATCH_MASK, IN_ACCESS, IN_MODIFY, IN_ATTRIB, IN_CLOSE_NOWRITE, IN_CLOSE_WRITE, \
    IN_OPEN, IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE, IN_DELETE_SELF, \
    IN_MOVE_SELF, IN_UNMOUNT, IN_Q_OVERFLOW, IN_IGNORED, IN_ONLYDIR, IN_DONT_FOLLOW, \
    IN_MASK_ADD, IN_ISDIR, IN_ONESHOT, IN_CLOSE, IN_MOVED, IN_CHANGED]


TRUE  = 0
FALSE = 1

NOT_STARTED = "NOT_STARTED"
STARTED     = "STARTED"
STOPPING    = "STOPPING"
STOPPED     = "STOPPED"

class INotifyEventHandler(FileSystemEventHandler):

    def __init__(self, callbacks, pending_delay):
        FileSystemEventHandler.__init__(self)
        self._callbacks = callbacks
        self._pending_delay = pending_delay
        self._pending = set()

    def process(self, event):
        event_path = event.src_path
        def _maybe_notify(path):
            if path not in self._pending:
                self._pending.add(path)
                def _do_callbacks():
                    self._pending.remove(path)
                    for cb in self._callbacks:
                        try:
                            cb(None, path, IN_CHANGED)
                        except Exception, e:
                            log.err(e)
                reactor.callLater(self._pending_delay, _do_callbacks)
        reactor.callFromThread(_maybe_notify, event_path)

    def on_any_event(self, event):
        self.process(event)

class INotify(PollMixin):
    """
    I am a prototype INotify, made to work on Mac OS X (Darwin)
    using the Watchdog python library. This is actually a subset
    of the twisted Linux INotify class because we only implement
    the following methods:
     - watch
     - startReading
     - stopReading
     - wait_until_stopped
     - set_pending_delay
    """
    def __init__(self):
        self._path = None
        self._pending_delay = 1.0
        self.recursive_includes_new_subdirectories = True
        self._observer = None
        self._state = NOT_STARTED

    def set_pending_delay(self, delay):
        self._pending_delay = delay

    def startReading(self):
        try:
            _assert(self._observer is not None, "no watch set")
            self._observer.schedule(INotifyEventHandler(self._callbacks, self._pending_delay), path=self._path)
            self._observer.start() # XXX this should execute in it's own thread ^
            self._state = STARTED
        except Exception, e:
            log.err(e)
            self._state = STOPPED
            raise

    def stopReading(self):
        # FIXME race conditions
        if self._state != STOPPED:
            self._state = STOPPING
        self._observer.stop()
        def is_stopped():
            self._observer.join()
            self._state = STOPPED
        reactor.callFromThread(is_stopped)

    def wait_until_stopped(self):
        fileutil.write(os.path.join(self._path, u".ignore-me"), "")
        return self.poll(lambda: self._state == STOPPED)

    def watch(self, path, mask=IN_WATCH_MASK, autoAdd=False, callbacks=None, recursive=False):
        precondition(self._state == NOT_STARTED, "watch() can only be called before startReading()", state=self._state)
        precondition(isinstance(autoAdd, bool), autoAdd=autoAdd)
        precondition(isinstance(recursive, bool), recursive=recursive)
        #precondition(autoAdd == recursive, "need autoAdd and recursive to be the same", autoAdd=autoAdd, recursive=recursive)

        path_u = path.path
        if not isinstance(path_u, unicode):
            path_u = path_u.decode(sys.getfilesystemencoding())
            _assert(isinstance(path_u, unicode), path_u=path_u)
        self._path = path_u
        self._recursive = TRUE if recursive else FALSE
        self._callbacks = callbacks or []
        self._observer = Observer()
