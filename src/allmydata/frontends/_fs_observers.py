"""
Present a simplified layer on top of the watchdog library which makes it
easier to use from the magic-folder implementation.
"""

from __future__ import unicode_literals, print_function

__all__ = [
    "DirMovedInEvent",
    "FileCloseWriteEvent",
    "get_observer",
]

debug = print

from unicodedata import normalize
from os import walk
from os.path import join

from watchdog.events import (
    FileSystemEvent,
    FileSystemMovedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileCreatedEvent,
    FileMovedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirCreatedEvent,
    DirMovedEvent
)
from watchdog.utils import UnsupportedLibc
from watchdog.observers.api import (
    BaseObserver,
    DEFAULT_OBSERVER_TIMEOUT
)

try:
    from watchdog.observers.inotify import (
        InotifyEmitter as _InotifyEmitter,
        InotifyObserver as _InotifyObserver,
    )
except UnsupportedLibc:
    # We must be on a platform that doesn't support inotify.
    _InotifyEmitter = None
    _InotifyConstants = None
    _InotifyObserver = None
else:
    # It would be nice if watchdog just parameterized event_mask.  See
    # https://github.com/gorakhargosh/watchdog/issues/520
    from watchdog.observers import inotify_buffer as _inotify_buffer
    from watchdog.observers.inotify_c import (
        InotifyConstants as _InotifyConstants,
        Inotify as _Inotify,
    )
    event_mask = (
        # TODO: what about IN_MOVE_SELF and IN_UNMOUNT?
        0
        | _InotifyConstants.IN_CREATE
        | _InotifyConstants.IN_CLOSE_WRITE
        | _InotifyConstants.IN_MOVED_TO
        | _InotifyConstants.IN_MOVED_FROM
        | _InotifyConstants.IN_DELETE
        | _InotifyConstants.IN_ONLYDIR
        | _InotifyConstants.IN_EXCL_UNLINK
    )
    def BetterInotify(path, recursive):
        # InotifyBuffer passes no event_mask to Inotify.  Inotify's default
        # event_mask is some events, not as many as we want.  Request the
        # events we want, here.
        return _Inotify(path, recursive, event_mask=event_mask)
    _inotify_buffer.Inotify = BetterInotify

try:
    from watchdog.utils.dirsnapshot import (
        DirectorySnapshot as _DirectorySnapshot,
    )
    from watchdog.observers.fsevents import (
        FSEventsEmitter as _FSEventsEmitter,
        FSEventsObserver as _FSEventsObserver,
    )
    import _watchdog_fsevents as _fsevents
except ImportError:
    _DirectorySnapshot = None
    _FSEventsEmitter = None
    _FSEventsObserver = None
    _fsevents = None

def get_observer():
    """
    Get a filesystem events observer appropriate for the runtime platform.
    """
    from watchdog.observers import Observer
    # Substitute our slightly modified observer, if appropriate.  They are
    # better suited to magic-folder.
    if Observer is _InotifyObserver:
        Observer = _SimplifiedInotifyObserver
    elif Observer is _FSEventsObserver:
        Observer = _ReorderedFSEventsObserver

    return Observer()


class FileCloseWriteEvent(FileSystemEvent):
    event_type = "file-close-write"


class DirMovedInEvent(FileSystemEvent):
    event_type = "dir-moved-in"


def _transform_inotify_event(event):
    """
    Convert a low-level-ish inotify event object to something a little bit
    more abstract.

    Really, just smash enough information into a simpler form to make
    magic-folder's job easier later on.
    """
    if isinstance(event, tuple):
        move_from, move_to = event
        yield FileSystemMovedEvent(move_from.src_path, move_to.src_path)
    elif event.is_close_write:
        yield FileCloseWriteEvent(event.src_path)
    elif not event.is_directory and event.is_create:
        # Skip emitting non-directory creation because magic-folder doesn't
        # want them.  There will be a future FileCloseWriteEvent that is
        # better.
        pass
    elif event.is_modify:
        # Skip emitting modify events because magic-folder doesn't want them,
        # either.  There will be a future FileCloseWriteEvent that is better.
        pass
    elif event.is_moved_to and event.is_directory:
        # If we get a move-to out of a tuple then it's not matched with a
        # move-from so it is being moved in from outside the watch area.
        # Synthesize a moved-in events for it and close-write events for
        # all files in it.
        #
        # It's not clear magic-folder actually needs the directory event
        # itself but if we omit it, counter values change and that is
        # literally the end of the world.
        yield DirMovedInEvent(event.src_path)
        for dirpath, dirnames, filenames in walk(event.src_path):
            for fname in filenames:
                p = join(event.src_path, dirpath, fname)
                yield FileCloseWriteEvent(p)
            for dname in dirnames:
                yield DirMovedInEvent(join(event.src_path, dirpath, dname))
    else:
        # We could be nicer and reflect the event type here.  magic-folder
        # doesn't really need it but it might improve logging.
        yield FileSystemEvent(event.src_path)


class _SimplifiedInotifyEmitter(_InotifyEmitter or object):
    """
    An emitter for inotify events that only does exactly the translation from
    low-level events to high-level events that magic-folders specifically
    needs.
    """
    def queue_events(self, timeout, full_events=False):
        """
        Override the inherited behavior to apply the high-level transformation and
        add new-directory-watching behavior.
        """
        with self._lock:
            event = self._inotify.read_event()
            if event is None:
                return
            high_level_events = _transform_inotify_event(event)
            for event in high_level_events:
                self.queue_event(event)


class _SimplifiedInotifyObserver(BaseObserver):
    """
    An inotify-based observer that uses a simplified event emitter.
    """
    def __init__(self, timeout=DEFAULT_OBSERVER_TIMEOUT):
        BaseObserver.__init__(
            self,
            # Use our emitter.  This is why _SimplifiedInotifyObserver exists.
            # It would be nice if emitter_class were a parameter to
            # InotifyObserver (or even just BaseObserver - less subclassing
            # for the win).
            emitter_class=_SimplifiedInotifyEmitter,
            timeout=timeout,
        )


class _ReorderedFSEventsEmitter(_FSEventsEmitter or object):
    """
    A modified fsevents emitter which delivers all directory-type change
    notifications before any file-type change notifications.

    This is helpful because it means you see `foo` get created before
    `foo/bar` when a directory gets moved into the observed area.  If the
    order is reversed, it's hard to know if you should re-scan `foo/bar`
    (rather, you have to assume you should) to determine the true state.
    """
    def queue_event(self, event):
        p_u = event.src_path.decode("utf-8")
        if normalize("NFD", p_u) != p_u:
            # assuming NFD version of the event is going to come along
            # shortly.
            debug("queue_event dropping event for non-NFD path {}")
            return

        debug("queue_event({})".format(event))
        return _FSEventsEmitter.queue_event(self, event)

    def queue_events(self, timeout):
        with self._lock:
            debug("queue_events")
            if not self.watch.is_recursive\
                and self.watch.path not in self.pathnames:
                return
            new_snapshot = _DirectorySnapshot(self.watch.path,
                                              self.watch.is_recursive)
            events = new_snapshot - self.snapshot
            self.snapshot = new_snapshot

            # Directories.
            for src_path in events.dirs_deleted:
                self.queue_event(DirDeletedEvent(src_path))
            for src_path in events.dirs_created:
                self.queue_event(DirCreatedEvent(src_path))
            for src_path, dest_path in events.dirs_moved:
                self.queue_event(DirMovedEvent(src_path, dest_path))

            # Files.
            for src_path in events.files_deleted:
                self.queue_event(FileDeletedEvent(src_path))
            for src_path in events.files_modified:
                self.queue_event(FileModifiedEvent(src_path))
            for src_path in events.files_created:
                self.queue_event(FileCreatedEvent(src_path))
            for src_path, dest_path in events.files_moved:
                self.queue_event(FileMovedEvent(src_path, dest_path))

    def run(self):
        try:
            def callback(pathnames, flags, emitter=self):
                debug("callback({}, {})".format(pathnames, flags))
                emitter.queue_events(emitter.timeout)
                debug("queue_events returned")

            self.pathnames = [self.watch.path]
            debug(
                "add_watch({})".format(
                    list(p.decode("utf-8") for p in self.pathnames),
                ),
            )
            _fsevents.add_watch(self,
                                self.watch,
                                callback,
                                self.pathnames)
            debug("read_events")
            import sys
            sys.stdout.flush()
            _fsevents.read_events(self)
            debug("run is done")
        except:
            print("_ReorderedFSEventsEmitter.run failed")
            import traceback
            traceback.print_exc()

class _ReorderedFSEventsObserver(_FSEventsObserver or object):
    def __init__(self, timeout=DEFAULT_OBSERVER_TIMEOUT):
        BaseObserver.__init__(self, emitter_class=_ReorderedFSEventsEmitter,
                              timeout=timeout)
