
import os, sys

from watchdog.observers import Observer  
from watchdog.events import FileSystemEventHandler  

from twisted.internet import reactor

from allmydata.util.assertutil import _assert, precondition
from allmydata.util import log, fileutil
from allmydata.util.fake_inotify import IN_CHANGED, IN_WATCH_MASK


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

class INotify(object):
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
    def __init__(self,  reactor=None):
        self._path = None
        self._pending_delay = 1.0
        self.recursive_includes_new_subdirectories = True
        self._observer = None

    def set_pending_delay(self, delay):
        self._pending_delay = delay

    def startReading(self):
        try:
            _assert(self._observer is not None, "no watch set")
            self._state = STARTED
            
            self._observer.schedule(INotifyEventHandler(self._callbacks, self._pending_delay), path=self._path)
            self._observer.start() # XXX this should execute in it's own thread ^
        except Exception, e:
            log.err(e)
            self._state = STOPPED
            raise

    def stopReading(self):
        # FIXME race conditions
        if self._state != STOPPED:
            self._state = STOPPING
        self._observer.stop()
        self._observer.join() # synchronous

    def wait_until_stopped(self):
        fileutil.write(os.path.join(self._path.path, u".ignore-me"), "")
        return self.poll(lambda: self._state == STOPPED)

    def watch(self, path, mask=IN_WATCH_MASK, autoAdd=False, callbacks=None, recursive=False):
        precondition(self._state == NOT_STARTED, "watch() can only be called before startReading()", state=self._state)
        precondition(isinstance(autoAdd, bool), autoAdd=autoAdd)
        precondition(isinstance(recursive, bool), recursive=recursive)
        #precondition(autoAdd == recursive, "need autoAdd and recursive to be the same", autoAdd=autoAdd, recursive=recursive)

        self._path = path
        path_u = path.path
        if not isinstance(path_u, unicode):
            path_u = path_u.decode(sys.getfilesystemencoding())
            _assert(isinstance(path_u, unicode), path_u=path_u)

        self._recursive = TRUE if recursive else FALSE
        self._callbacks = callbacks or []
        self._observer = Observer()
