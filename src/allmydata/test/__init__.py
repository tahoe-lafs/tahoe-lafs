
from foolscap.logging.incident import IncidentQualifier
class NonQualifier(IncidentQualifier):
    def check_event(self, ev):
        return False

def disable_foolscap_incidents():
    # Foolscap-0.2.9 (at least) uses "trailing delay" in its default incident
    # reporter: after a severe log event is recorded (thus triggering an
    # "incident" in which recent events are dumped to a file), a few seconds
    # of subsequent events are also recorded in the incident file. The timer
    # that this leaves running will cause "Unclean Reactor" unit test
    # failures. The simplest workaround is to disable this timer. Note that
    # this disables the timer for the entire process: do not call this from
    # regular runtime code; only use it for unit tests that are running under
    # Trial.
    #IncidentReporter.TRAILING_DELAY = None
    #
    # Also, using Incidents more than doubles the test time. So we just
    # disable them entirely.
    from foolscap.logging.log import theLogger
    iq = NonQualifier()
    theLogger.setIncidentQualifier(iq)

# we disable incident reporting for all unit tests.
disable_foolscap_incidents()

import sys
if sys.platform == "win32":
    from allmydata.windows.fixups import initialize
    initialize()
