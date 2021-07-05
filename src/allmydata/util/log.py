"""
Logging utilities.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
from six import ensure_str

from pyutil import nummedobj

from foolscap.logging import log
from twisted.python import log as tw_log

if PY2:
    def bytes_to_unicode(ign, obj):
        return obj
else:
    # We want to convert bytes keys to Unicode, otherwise JSON serialization
    # inside foolscap will fail (for details see
    # https://github.com/warner/foolscap/issues/88)
    from .jsonbytes import bytes_to_unicode


NOISY = log.NOISY # 10
OPERATIONAL = log.OPERATIONAL # 20
UNUSUAL = log.UNUSUAL # 23
INFREQUENT = log.INFREQUENT # 25
CURIOUS = log.CURIOUS # 28
WEIRD = log.WEIRD # 30
SCARY = log.SCARY # 35
BAD = log.BAD # 40


def msg(*args, **kwargs):
    return log.msg(*args, **bytes_to_unicode(True, kwargs))

# If log.err() happens during a unit test, the unit test should fail. We
# accomplish this by sending it to twisted.log too. When a WEIRD/SCARY/BAD
# thing happens that is nevertheless handled, use log.msg(failure=f,
# level=WEIRD) instead.

def err(failure=None, _why=None, **kwargs):
    tw_log.err(failure, _why, **kwargs)
    if 'level' not in kwargs:
        kwargs['level'] = log.UNUSUAL
    return log.err(failure, _why, **bytes_to_unicode(True, kwargs))

class LogMixin(object):
    """ I remember a msg id and a facility and pass them to log.msg() """
    def __init__(self, facility=None, grandparentmsgid=None):
        self._facility = facility
        self._grandparentmsgid = grandparentmsgid
        self._parentmsgid = None

    def log(self, msg, facility=None, parent=None, *args, **kwargs):
        if facility is None:
            facility = self._facility
        pmsgid = parent
        if pmsgid is None:
            pmsgid = self._parentmsgid
            if pmsgid is None:
                pmsgid = self._grandparentmsgid
        kwargs = {ensure_str(k): v for (k, v) in kwargs.items()}
        msgid = log.msg(msg, facility=facility, parent=pmsgid, *args,
                        **bytes_to_unicode(True, kwargs))
        if self._parentmsgid is None:
            self._parentmsgid = msgid
        return msgid

class PrefixingLogMixin(nummedobj.NummedObj, LogMixin):
    """ I prepend a prefix to each msg, which includes my class and instance number as well as
    a prefix supplied by my subclass. """
    def __init__(self, facility=None, grandparentmsgid=None, prefix=''):
        nummedobj.NummedObj.__init__(self)
        LogMixin.__init__(self, facility, grandparentmsgid)

        if prefix:
            if isinstance(prefix, bytes):
                prefix = prefix.decode("utf-8", errors="replace")
            self._prefix = "%s(%s): " % (self.__repr__(), prefix)
        else:
            self._prefix = "%s: " % (self.__repr__(),)

    def log(self, msg="", facility=None, parent=None, *args, **kwargs):
        return LogMixin.log(self, self._prefix + msg, facility, parent, *args, **kwargs)
