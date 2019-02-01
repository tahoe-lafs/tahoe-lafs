"""
Present a simplified layer on top of the watchdog library which makes it
easier to use from the magic-folder implementation.
"""

from __future__ import unicode_literals

__all__ = [
    "DirMovedInEvent",
    "FileCloseWriteEvent",
    "get_observer",
]

from os import walk
from os.path import join

from watchdog.events import (
    FileSystemEvent,
    FileSystemMovedEvent,
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


def get_observer():
    """
    Get a filesystem events observer appropriate for the runtime platform.
    """
    from watchdog.observers import Observer
    if Observer is _InotifyObserver:
        # Substitute our slightly modified observer.  It is better suited to
        # magic-folder.
        Observer = _SimplifiedInotifyObserver

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
                if isinstance(event, DirMovedInEvent):
                    # Arrange for it to be watched as well.  Another option
                    # would be to have the magic-folder code do this in the
                    # `pathinfo.isdir` case of `Uploader._process`.
                    self._inotify._inotify.add_watch(event.src_path)
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
