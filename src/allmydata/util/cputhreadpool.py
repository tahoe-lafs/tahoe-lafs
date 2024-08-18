"""
A global thread pool for CPU-intensive tasks.

Motivation:

* Certain tasks are blocking on CPU, and so should be run in a thread.
* The Twisted thread pool is used for operations that don't necessarily block
  on CPU, like DNS lookups.  CPU processing should not block DNS lookups!
* The number of threads should be fixed, and tied to the number of available
  CPUs.

As a first pass, this uses ``os.cpu_count()`` to determine the max number of
threads.  This may create too many threads, as it doesn't cover things like
scheduler affinity or cgroups, but that's not the end of the world.
"""

import os
from typing import TypeVar, Callable, cast
from functools import partial
import threading
from typing_extensions import ParamSpec
from unittest import TestCase

from twisted.python.threadpool import ThreadPool
from twisted.internet.threads import deferToThreadPool
from twisted.internet import reactor
from twisted.internet.interfaces import IReactorFromThreads

_CPU_THREAD_POOL = ThreadPool(minthreads=0, maxthreads=os.cpu_count() or 1, name="TahoeCPU")
if hasattr(threading, "_register_atexit"):
    # This is a private API present in Python 3.8 or later, specifically
    # designed for thread pool shutdown. Since it's private, it might go away
    # at any point, so if it doesn't exist we still have a solution.
    threading._register_atexit(_CPU_THREAD_POOL.stop)  # type: ignore
else:
    # Daemon threads allow shutdown to happen without any explicit stopping of
    # threads. There are some bugs in old Python versions related to daemon
    # threads (fixed in subsequent CPython patch releases), but Python's own
    # thread pools use daemon threads in those versions so we're no worse off.
    _CPU_THREAD_POOL.threadFactory = partial(  # type: ignore
        _CPU_THREAD_POOL.threadFactory, daemon=True
    )
_CPU_THREAD_POOL.start()


P = ParamSpec("P")
R = TypeVar("R")

# Is running in a thread pool disabled? Should only be true in synchronous unit
# tests.
_DISABLED = False


async def defer_to_thread(f: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """
    Run the function in a thread, return the result.

    However, if ``disable_thread_pool_for_test()`` was called the function will
    be called synchronously inside the current thread.

    To reduce chances of synchronous tests being misleading as a result, this
    is an async function on presumption that will encourage immediate ``await``ing.
    """
    if _DISABLED:
        return f(*args, **kwargs)

    # deferToThreadPool has no type annotations...
    result = await deferToThreadPool(cast(IReactorFromThreads, reactor), _CPU_THREAD_POOL, f, *args, **kwargs)
    return result


def disable_thread_pool_for_test(test: TestCase) -> None:
    """
    For the duration of the test, calls to ``defer_to_thread()`` will actually
    run synchronously, which is useful for synchronous unit tests.
    """
    global _DISABLED

    def restore():
        global _DISABLED
        _DISABLED = False

    test.addCleanup(restore)

    _DISABLED = True


__all__ = ["defer_to_thread", "disable_thread_pool_for_test"]
