
from zope.interface import Interface, implements
from allmydata.util import observer

class IMonitor(Interface):
    """I manage status, progress, and cancellation for long-running operations.

    Whoever initiates the operation should create a Monitor instance and pass
    it into the code that implements the operation. That code should
    periodically check in with the Monitor, perhaps after each major unit of
    work has been completed, for two purposes.

    The first is to inform the Monitor about progress that has been made, so
    that external observers can be reassured that the operation is proceeding
    normally. If the operation has a well-known amount of work to perform,
    this notification should reflect that, so that an ETA or 'percentage
    complete' value can be derived.

    The second purpose is to check to see if the operation has been
    cancelled. The impatient observer who no longer wants the operation to
    continue will inform the Monitor; the next time the operation code checks
    in, it should notice that the operation has been cancelled, and wrap
    things up. The same monitor can be passed to multiple operations, all of
    which may check for cancellation: this pattern may be simpler than having
    the original caller keep track of subtasks and cancel them individually.
    """

    # the following methods are provided for the operation code

    def is_cancelled(self):
        """Returns True if the operation has been cancelled. If True,
        operation code should stop creating new work, and attempt to stop any
        work already in progress."""

    def set_status(self, status):
        """Sets the Monitor's 'status' object to an arbitrary value.
        Different operations will store different sorts of status information
        here. Operation code should use get+modify+set sequences to update
        this."""

    def get_status(self):
        """Return the status object."""

    def finish(self, status):
        """Call this when the operation is done, successful or not. The
        Monitor's lifetime is influenced by the completion of the operation
        it is monitoring. The Monitor's 'status' value will be set with the
        'status' argument, just as if it had been passed to set_status().
        This value will be used to fire the Deferreds that are returned by
        when_done().

        Operations that fire a Deferred when they finish should trigger this
        with d.addBoth(monitor.finish)"""

    # the following methods are provided for the initiator of the operation

    def is_finished(self):
        """Return a boolean, True if the operation is done (whether
        successful or failed), False if it is still running."""

    def when_done(self):
        """Return a Deferred that fires when the operation is complete. It
        will fire with the operation status, the same value as returned by
        get_status()."""

    def cancel(self):
        """Cancel the operation as soon as possible. is_cancelled() will
        start returning True after this is called."""

    #   get_status() is useful too, but it is operation-specific

class Monitor:
    implements(IMonitor)

    def __init__(self):
        self.cancelled = False
        self.finished = False
        self.status = None
        self.observer = observer.OneShotObserverList()

    def is_cancelled(self):
        return self.cancelled

    def is_finished(self):
        return self.finished

    def when_done(self):
        return self.observer.when_fired()

    def cancel(self):
        self.cancelled = True

    def finish(self, status_or_failure):
        self.set_status(status_or_failure)
        self.finished = True
        self.observer.fire(status_or_failure)
        return status_or_failure

    def get_status(self):
        return self.status
    def set_status(self, status):
        self.status = status
