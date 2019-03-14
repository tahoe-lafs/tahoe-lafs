
"""
An implementation of an inotify-like interface on top of the ``watchdog`` library.
"""

from __future__ import (
    unicode_literals,
    print_function,
    absolute_import,
    division,
)

__all__ = [
    "humanReadableMask", "INotify",
    "IN_WATCH_MASK", "IN_ACCESS", "IN_MODIFY", "IN_ATTRIB", "IN_CLOSE_NOWRITE",
    "IN_CLOSE_WRITE", "IN_OPEN", "IN_MOVED_FROM", "IN_MOVED_TO", "IN_CREATE",
    "IN_DELETE", "IN_DELETE_SELF", "IN_MOVE_SELF", "IN_UNMOUNT", "IN_ONESHOT",
    "IN_Q_OVERFLOW", "IN_IGNORED", "IN_ONLYDIR", "IN_DONT_FOLLOW", "IN_MOVED",
    "IN_MASK_ADD", "IN_ISDIR", "IN_CLOSE", "IN_CHANGED", "_FLAG_TO_HUMAN",
]

from watchdog.observers import Observer
from watchdog.events import (
    FileSystemEvent,
    FileSystemEventHandler, DirCreatedEvent, FileCreatedEvent,
    DirDeletedEvent, FileDeletedEvent, FileModifiedEvent
)

from twisted.internet import reactor
from twisted.python.filepath import FilePath
from allmydata.util.fileutil import abspath_expanduser_unicode

from eliot import (
    ActionType,
    Message,
    Field,
    preserve_context,
    start_action,
)

from allmydata.util.pollmixin import PollMixin
from allmydata.util.assertutil import _assert, precondition
from allmydata.util import encodingutil
from allmydata.util.fake_inotify import humanReadableMask, \
    IN_WATCH_MASK, IN_ACCESS, IN_MODIFY, IN_ATTRIB, IN_CLOSE_NOWRITE, IN_CLOSE_WRITE, \
    IN_OPEN, IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE, IN_DELETE_SELF, \
    IN_MOVE_SELF, IN_UNMOUNT, IN_Q_OVERFLOW, IN_IGNORED, IN_ONLYDIR, IN_DONT_FOLLOW, \
    IN_MASK_ADD, IN_ISDIR, IN_ONESHOT, IN_CLOSE, IN_MOVED, IN_CHANGED, \
    _FLAG_TO_HUMAN

from ..util.eliotutil import (
    MAYBE_NOTIFY,
    CALLBACK,
    validateInstanceOf,
)

from . import _watchdog_541

_watchdog_541.patch()

NOT_STARTED = "NOT_STARTED"
STARTED     = "STARTED"
STOPPING    = "STOPPING"
STOPPED     = "STOPPED"

_PATH = Field.for_types(
    u"path",
    [bytes, unicode],
    u"The path an inotify event concerns.",
)

_EVENT = Field(
    u"event",
    lambda e: e.__class__.__name__,
    u"The watchdog event that has taken place.",
    validateInstanceOf(FileSystemEvent),
)

ANY_INOTIFY_EVENT = ActionType(
    u"watchdog:inotify:any-event",
    [_PATH, _EVENT],
    [],
    u"An inotify event is being dispatched.",
)

class INotifyEventHandler(FileSystemEventHandler):
    def __init__(self, path, mask, callbacks, pending_delay):
        FileSystemEventHandler.__init__(self)
        self._path = path
        self._mask = mask
        self._callbacks = callbacks
        self._pending_delay = pending_delay
        self._pending = set()

    def _maybe_notify(self, path, event):
        with MAYBE_NOTIFY():
            event_mask = IN_CHANGED
            if isinstance(event, FileModifiedEvent):
                event_mask = event_mask | IN_CLOSE_WRITE
                event_mask = event_mask | IN_MODIFY
            if isinstance(event, (DirCreatedEvent, FileCreatedEvent)):
                # For our purposes, IN_CREATE is irrelevant.
                event_mask = event_mask | IN_CLOSE_WRITE
            if isinstance(event, (DirDeletedEvent, FileDeletedEvent)):
                event_mask = event_mask | IN_DELETE
            if event.is_directory:
                event_mask = event_mask | IN_ISDIR
            if not (self._mask & event_mask):
                return
            for cb in self._callbacks:
                try:
                    with CALLBACK(inotify_events=event_mask):
                        cb(None, FilePath(path), event_mask)
                except:
                    # Eliot already logged the exception for us.
                    # There's nothing else we can do about it here.
                    pass

    def process(self, event):
        event_filepath_u = event.src_path.decode(encodingutil.get_filesystem_encoding())
        event_filepath_u = abspath_expanduser_unicode(event_filepath_u, base=self._path)

        if event_filepath_u == self._path:
            # ignore events for parent directory
            return

        self._maybe_notify(event_filepath_u, event)

    def on_any_event(self, event):
        with ANY_INOTIFY_EVENT(path=event.src_path, event=event):
            reactor.callFromThread(
                preserve_context(self.process),
                event,
            )


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
        self._callbacks = {}
        self._watches = {}
        self._state = NOT_STARTED
        self._observer = Observer(timeout=self._pending_delay)

    def set_pending_delay(self, delay):
        Message.log(message_type=u"watchdog:inotify:set-pending-delay", delay=delay)
        assert self._state != STARTED
        self._pending_delay = delay

    def startReading(self):
        with start_action(action_type=u"watchdog:inotify:start-reading"):
            assert self._state != STARTED
            try:
                # XXX twisted.internet.inotify doesn't require watches to
                # be set before startReading is called.
                # _assert(len(self._callbacks) != 0, "no watch set")
                self._observer.start()
                self._state = STARTED
            except:
                self._state = STOPPED
                raise

    def stopReading(self):
        with start_action(action_type=u"watchdog:inotify:stop-reading"):
            if self._state != STOPPED:
                self._state = STOPPING
            self._observer.unschedule_all()
            self._observer.stop()
            self._observer.join()
            self._state = STOPPED

    def wait_until_stopped(self):
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

        path_u = path.path
        if not isinstance(path_u, unicode):
            path_u = path_u.decode('utf-8')
            _assert(isinstance(path_u, unicode), path_u=path_u)

        if path_u not in self._callbacks.keys():
            self._callbacks[path_u] = callbacks or []
            self._watches[path_u] = self._observer.schedule(
                INotifyEventHandler(path_u, mask, self._callbacks[path_u], self._pending_delay),
                path=path_u,
                recursive=False,
            )
