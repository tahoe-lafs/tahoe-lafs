
import nummedobj

from foolscap.logging import log
from twisted.python import log as tw_log

NOISY = log.NOISY # 10
OPERATIONAL = log.OPERATIONAL # 20
UNUSUAL = log.UNUSUAL # 23
INFREQUENT = log.INFREQUENT # 25
CURIOUS = log.CURIOUS # 28
WEIRD = log.WEIRD # 30
SCARY = log.SCARY # 35
BAD = log.BAD # 40


msg = log.msg

# If log.err() happens during a unit test, the unit test should fail. We
# accomplish this by sending it to twisted.log too. When a WEIRD/SCARY/BAD
# thing happens that is nevertheless handled, use log.msg(failure=f,
# level=WEIRD) instead.

def err(*args, **kwargs):
    tw_log.err(*args, **kwargs)
    if 'level' not in kwargs:
        kwargs['level'] = log.UNUSUAL
    return log.err(*args, **kwargs)

class LogMixin(object):
    """ I remember a msg id and a facility and pass them to log.msg() """
    def __init__(self, facility=None, grandparentmsgid=None):
        self._facility = facility
        self._grandparentmsgid = grandparentmsgid
        self._parentmsgid = None

    def log(self, msg, facility=None, parent=None, *args, **kwargs):
        if facility is None:
            facility = self._facility
        if parent is None:
            pmsgid = self._parentmsgid
        if pmsgid is None:
            pmsgid = self._grandparentmsgid
        msgid = log.msg(msg, facility=facility, parent=pmsgid, *args, **kwargs)
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
            self._prefix = "%s(%s): " % (self.__repr__(), prefix)
        else:
            self._prefix = "%s: " % (self.__repr__(),)

    def log(self, msg, facility=None, parent=None, *args, **kwargs):
        return LogMixin.log(self, self._prefix + msg, facility, parent, *args, **kwargs)
