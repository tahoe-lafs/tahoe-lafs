import sys
import traceback
import signal
import threading

from twisted.internet import reactor


def print_stacks():
    print("Uh oh, something is blocking the event loop!")
    current_thread = threading.get_ident()
    for thread_id, frame in sys._current_frames().items():
        if thread_id == current_thread:
            traceback.print_stack(frame, limit=10)
            break


def catch_blocking_in_event_loop(test=None):
    """
    Print tracebacks if the event loop is blocked for more than a short amount
    of time.
    """
    signal.signal(signal.SIGALRM, lambda *args: print_stacks())

    current_scheduled = [None]

    def cancel_and_rerun():
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.setitimer(signal.ITIMER_REAL, 0.015)
        current_scheduled[0] = reactor.callLater(0.01, cancel_and_rerun)

    cancel_and_rerun()

    def cleanup():
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.setitimer(signal.ITIMER_REAL, 0)
        current_scheduled[0].cancel()

    if test is not None:
        test.addCleanup(cleanup)
