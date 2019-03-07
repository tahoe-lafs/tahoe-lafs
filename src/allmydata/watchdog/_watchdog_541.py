"""
Hotfix for https://github.com/gorakhargosh/watchdog/issues/541
"""

from watchdog.observers.fsevents import FSEventsEmitter

# The class object has already been bundled up in the default arguments to
# FSEventsObserver.__init__.  So mutate the class object (instead of replacing
# it with a safer version).
original_on_thread_stop = FSEventsEmitter.on_thread_stop
def safe_on_thread_stop(self):
    if self.is_alive():
        return original_on_thread_stop(self)

def patch():
    FSEventsEmitter.on_thread_stop = safe_on_thread_stop
