"""
Helpers for managing garbage collection.

:ivar fileDescriptorResource: A garbage-collection-informing resource tracker
    for file descriptors.  This is used to trigger a garbage collection when
    it may be possible to reclaim a significant number of file descriptors as
    a result.  Register allocation and release of *bare* file descriptors with
    this object (file objects, socket objects, etc, have their own integration
    with the garbage collector and don't need to bother with this).

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

__all__ = [
    "fileDescriptorResource",
]

import gc

import attr

@attr.s
class _ResourceTracker(object):
    """
    Keep track of some kind of resource and trigger a full garbage collection
    when allocations outnumber releases by some amount.

    :ivar int _counter: The number of allocations that have happened in excess
        of releases since the last full collection triggered by this tracker.

    :ivar int _threshold: The number of excess allocations at which point a
        full collection will be triggered.
    """
    _counter = attr.ib(default=0)
    _threshold = attr.ib(default=25)

    def allocate(self):
        """
        Register the allocation of an instance of this resource.
        """
        self._counter += 1
        if self._counter > self._threshold:
            gc.collect()
            # Garbage collection of this resource has done what it can do.  If
            # nothing was collected, it doesn't make any sense to trigger
            # another full collection the very next time the resource is
            # allocated.  Start the counter over again.  The next collection
            # happens when we again exceed the threshold.
            self._counter = 0


    def release(self):
        """
        Register the release of an instance of this resource.
        """
        if self._counter > 0:
            # If there were any excess allocations at this point, account for
            # there now being one fewer.  It is not helpful to allow the
            # counter to go below zero (as naturally would if a collection is
            # triggered and then subsequently resources are released).  In
            # that case, we would be operating as if we had set a higher
            # threshold and that is not desired.
            self._counter -= 1

fileDescriptorResource = _ResourceTracker()
