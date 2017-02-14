
from watchdog.observers import Observer
from watchdog.events import (
    FileSystemEventHandler, DirCreatedEvent, FileCreatedEvent,
    DirDeletedEvent, FileDeletedEvent, FileModifiedEvent
)


from twisted.internet import reactor
from twisted.python.filepath import FilePath
from allmydata.util.fileutil import abspath_expanduser_unicode

from allmydata.util.pollmixin import PollMixin
from allmydata.util.assertutil import _assert, precondition
from allmydata.util import log, encodingutil
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

    def __init__(self, path, mask, callbacks, pending_delay):
        print "init INotifyEventHandler"
        FileSystemEventHandler.__init__(self)
        self._path = path
        self._mask = mask
        self._callbacks = callbacks
        self._pending_delay = pending_delay
        self._pending = set()

    def process(self, event):
        print "FILESYSTEM ENCODING: %s" % encodingutil.get_filesystem_encoding()
        event_filepath_u = event.src_path.decode(encodingutil.get_filesystem_encoding())
        event_filepath_u = abspath_expanduser_unicode(event_filepath_u, base=self._path)

        if event_filepath_u == self._path:
            # ignore events for parent directory
            return

        def _maybe_notify(path):
            try:
                if path in self._pending:
                    return
                self._pending.add(path)
                def _do_callbacks():
                    self._pending.remove(path)
                    event_mask = IN_CHANGED
                    if isinstance(event, FileModifiedEvent):
                        event_mask = event_mask | IN_CLOSE_WRITE
                        event_mask = event_mask | IN_MODIFY
                    if isinstance(event, (DirCreatedEvent, FileCreatedEvent)):
                        event_mask = event_mask | IN_CLOSE_WRITE
                    if isinstance(event, (DirDeletedEvent, FileDeletedEvent)):
                        event_mask = event_mask | IN_DELETE
                    if event.is_directory:
                        event_mask = event_mask | IN_ISDIR
                    if not (self._mask & event_mask):
                        return
                    for cb in self._callbacks:
                        try:
                            cb(None, FilePath(path), event_mask)
                        except Exception, e:
                            print e
                            log.err(e)
                _do_callbacks()
            except Exception as e:
                print("BAD STUFF", e)
        reactor.callFromThread(_maybe_notify, event_filepath_u)

    def on_any_event(self, event):
        print "PROCESS EVENT %r" % (event,)
        self.process(event)

class INotify(PollMixin):
    """
    I am a prototype INotify, made to work on Mac OS X (Darwin)
    using the Watchdog python library. This is actually a simplified subset
    of the twisted Linux INotify class because we do not utilize the watch mask
    and only implement the following methods:
     - watch
     - startReading
     - stopReading
     - wait_until_stopped
     - set_pending_delay
    """
    def __init__(self):
        self._pending_delay = 1.0
        self.recursive_includes_new_subdirectories = False
        self._observer = Observer(timeout=self._pending_delay)
        self._callbacks = {}
        self._watches = {}
        self._state = NOT_STARTED

    def set_pending_delay(self, delay):
        print "set pending delay"
        assert self._state != STARTED
        self._pending_delay = delay
        self._observer = Observer(timeout=self._pending_delay)

    def startReading(self):
        print "START READING BEGIN"
        assert self._state != STARTED
        try:
            # XXX twisted.internet.inotify doesn't require watches to
            # be set before startReading is called.
            # _assert(len(self._callbacks) != 0, "no watch set")
            self._observer.start()
            self._state = STARTED
        except Exception, e:
            log.err(e)
            self._state = STOPPED
            raise
        print "START READING END"

    def stopReading(self):
        print "stopReading begin"
        if self._state != STOPPED:
            self._state = STOPPING
        self._observer.unschedule_all()
        self._observer.stop()
        self._observer.join()
        self._state = STOPPED
        print "stopReading end"

    def wait_until_stopped(self):
        print "wait until stopped"
        return self.poll(lambda: self._state == STOPPED)

    def _isWatched(self, path_u):
        return path_u in self._callbacks.keys()

    def ignore(self, path):
        path_u = path.path
        self._observer.unschedule(self._watches[path_u])
        del self._callbacks[path_u]
        del self._watches[path_u]

    def watch(self, path, mask=IN_WATCH_MASK, autoAdd=False, callbacks=None, recursive=False):
        precondition(isinstance(autoAdd, bool), autoAdd=autoAdd)
        precondition(isinstance(recursive, bool), recursive=recursive)
        assert autoAdd == False

        recursive = False
        self._recursive = TRUE if recursive else FALSE
        path_u = path.path
        if not isinstance(path_u, unicode):
            path_u = path_u.decode('utf-8')
            _assert(isinstance(path_u, unicode), path_u=path_u)

        if path_u not in self._callbacks.keys():
            self._callbacks[path_u] = callbacks or []
            self._watches[path_u] = self._observer.schedule(
                INotifyEventHandler(path_u, mask, self._callbacks[path_u], self._pending_delay),
                path=path_u,
                recursive=recursive)
