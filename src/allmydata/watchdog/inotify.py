
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemMovedEvent, FileModifiedEvent, DirModifiedEvent, FileCreatedEvent, FileDeletedEvent, DirCreatedEvent, DirDeletedEvent

from twisted.internet import reactor
from twisted.python.filepath import FilePath
#from twisted.python.filepath import InsecurePath

from allmydata.util.pollmixin import PollMixin
from allmydata.util.assertutil import _assert, precondition
from allmydata.util import log
from allmydata.util.encodingutil import unicode_from_filepath
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

    def is_enabled_for_mask(self, event):
        if isinstance(event, FileSystemMovedEvent) and self._mask & (IN_MOVED_TO | IN_MOVED_FROM):
            return True
        if (isinstance(event, FileModifiedEvent) or isinstance(event, DirModifiedEvent)) \
           and self._mask & (IN_CLOSE_WRITE | IN_CHANGED):
            return True
        if (isinstance(event, FileCreatedEvent) or isinstance(event, DirCreatedEvent)) and self._mask & IN_CREATE:
            return True
        if (isinstance(event, FileDeletedEvent) or isinstance(event, DirDeletedEvent)) and self._mask & IN_DELETE:
            return True
        return False

    def get_event_mask(self, event):
        if event.is_directory:
            mask = IN_ISDIR
        else:
            mask = 0 # XXX
        if isinstance(event, FileModifiedEvent) or isinstance(event, DirModifiedEvent):
            mask = IN_CHANGED
        if isinstance(event, FileDeletedEvent) or isinstance(event, DirDeletedEvent):
            mask = IN_DELETE
        if isinstance(event, FileCreatedEvent) or isinstance(event, DirCreatedEvent):
            mask = IN_CREATE
        return mask

    def process(self, event):
        event_filepath_u = event.src_path.decode(encodingutil.get_filesystem_encoding())

        if event_filepath_u == unicode_from_filepath(self._path):
            # ignore events for parent directory
            return
        #if not self.is_masked(event):
        #    return
        try:
            event_path = self._path.preauthChild(event.src_path)
        except InsecurePath, e:
            print "failed, child path outside watch directory: %r" % (e,)
            return

        def _maybe_notify(path):
            if path in self._pending:
                return
            self._pending.add(path)
            def _do_callbacks():
                print "DO CALLBACKS"
                self._pending.remove(path)
                event_mask = self.get_event_mask(event)
                for cb in self._callbacks:
                    try:
                        cb(None, FilePath(path), event_mask)
                    except Exception, e:
                        log.err(e)
            _do_callbacks()
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
        self._state = NOT_STARTED

    def set_pending_delay(self, delay):
        print "set pending delay"
        self._pending_delay = delay
        self._observer = Observer(timeout=self._pending_delay)

    def startReading(self):
        print "START READING BEGIN"
        try:
            _assert(len(self._callbacks) != 0, "no watch set")
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
        self._observer.stop()
        self._observer.join()
        self._state = STOPPED
        print "stopReading end"

    def wait_until_stopped(self):
        print "wait until stopped"
        return self.poll(lambda: self._state == STOPPED)

    def watch(self, path, mask=IN_WATCH_MASK, autoAdd=False, callbacks=None, recursive=False):
        precondition(isinstance(autoAdd, bool), autoAdd=autoAdd)
        precondition(isinstance(recursive, bool), recursive=recursive)

        self._recursive = TRUE if recursive else FALSE
        path_u = path.path
        if not isinstance(path_u, unicode):
            path_u = path_u.decode('utf-8')
            _assert(isinstance(path_u, unicode), path_u=path_u)

        if path_u not in self._callbacks.keys():
            self._callbacks[path_u] = callbacks or []
            self._observer.schedule(INotifyEventHandler(path, mask, self._callbacks[path_u], \
                                                        self._pending_delay), path=path_u, recursive=True)
