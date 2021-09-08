# -*- coding: utf-8 -*-
# Tahoe-LAFS -- secure, distributed storage grid
#
# Copyright © 2020 The Tahoe-LAFS Software Foundation
#
# This file is part of Tahoe-LAFS.
#
# See the docs/about.rst file for licensing information.

"""
Some setup that should apply across the entire test suite.

Rather than defining interesting APIs for other code to use, this just causes
some side-effects which make things better when the test suite runs.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from traceback import extract_stack, format_list

from foolscap.pb import Listener
from twisted.python.log import err
from twisted.application import service

from foolscap.logging.incident import IncidentQualifier


class NonQualifier(IncidentQualifier, object):
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


def _configure_hypothesis():
    from os import environ

    from hypothesis import (
        HealthCheck,
        settings,
    )

    settings.register_profile(
        "ci",
        suppress_health_check=[
            # CPU resources available to CI builds typically varies
            # significantly from run to run making it difficult to determine
            # if "too slow" data generation is a result of the code or the
            # execution environment.  Prevent these checks from
            # (intermittently) failing tests that are otherwise fine.
            HealthCheck.too_slow,
        ],
        # With the same reasoning, disable the test deadline.
        deadline=None,
    )

    profile_name = environ.get("TAHOE_LAFS_HYPOTHESIS_PROFILE", "default")
    settings.load_profile(profile_name)
_configure_hypothesis()

def logging_for_pb_listener():
    """
    Make Foolscap listen error reports include Listener creation stack
    information.
    """
    original__init__ = Listener.__init__
    def _listener__init__(self, *a, **kw):
        original__init__(self, *a, **kw)
        # Capture the stack here, where Listener is instantiated.  This is
        # likely to explain what code is responsible for this Listener, useful
        # information to have when the Listener eventually fails to listen.
        self._creation_stack = extract_stack()

    # Override the Foolscap implementation with one that has an errback
    def _listener_startService(self):
        service.Service.startService(self)
        d = self._ep.listen(self)
        def _listening(lp):
            self._lp = lp
        d.addCallbacks(
            _listening,
            # Make sure that this listen failure is reported promptly and with
            # the creation stack.
            err,
            errbackArgs=(
                "Listener created at {}".format(
                    "".join(format_list(self._creation_stack)),
                ),
            ),
        )
    Listener.__init__ = _listener__init__
    Listener.startService = _listener_startService
logging_for_pb_listener()

import sys
if sys.platform == "win32":
    from allmydata.windows.fixups import initialize
    initialize()

from eliot import to_file
from allmydata.util.jsonbytes import AnyBytesJSONEncoder
to_file(open("eliot.log", "wb"), encoder=AnyBytesJSONEncoder)
