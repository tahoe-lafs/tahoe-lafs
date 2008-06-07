import sys, os, Queue, atexit

dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dir = os.path.join(dir, 'pypeers')
if dir not in sys.path:
    sys.path.append(dir)
del dir

from greensock import *
import threadchannel


def _read_from_kernel(handler):
    while True:
        msg = read(handler.fd, handler.MAX_READ)
        if not msg:
            print >> sys.stderr, "out-kernel connexion closed"
            break
        autogreenlet(handler.handle_message, msg)

def add_handler(handler):
    autogreenlet(_read_from_kernel, handler)
    atexit.register(handler.close)

# ____________________________________________________________

THREAD_QUEUE = None

def thread_runner(n):
    while True:
        #print 'thread runner %d waiting' % n
        operation, answer = THREAD_QUEUE.get()
        #print 'thread_runner %d: %r' % (n, operation)
        try:
            res = True, operation()
        except Exception:
            res = False, sys.exc_info()
        #print 'thread_runner %d: got %d bytes' % (n, len(res or ''))
        answer.send(res)


def start_bkgnd_thread():
    global THREAD_QUEUE, THREAD_LOCK
    import thread
    threadchannel.startup()
    THREAD_LOCK = thread.allocate_lock()
    THREAD_QUEUE = Queue.Queue()
    for i in range(4):
        thread.start_new_thread(thread_runner, (i,))

def wget(*args, **kwds):
    from wget import wget

    def operation():
        kwds['unlock'] = THREAD_LOCK
        THREAD_LOCK.acquire()
        try:
            return wget(*args, **kwds)
        finally:
            THREAD_LOCK.release()

    if THREAD_QUEUE is None:
        start_bkgnd_thread()
    answer = threadchannel.ThreadChannel()
    THREAD_QUEUE.put((operation, answer))
    ok, res = answer.receive()
    if not ok:
        typ, value, tb = res
        raise typ, value, tb
    #print 'wget returns %d bytes' % (len(res or ''),)
    return res
