
from foolscap.logging import log
from twisted.python import failure

NOISY = log.NOISY # 10
OPERATIONAL = log.OPERATIONAL # 20
UNUSUAL = log.UNUSUAL # 23
INFREQUENT = log.INFREQUENT # 25
CURIOUS = log.CURIOUS # 28
WEIRD = log.WEIRD # 30
SCARY = log.SCARY # 35
BAD = log.BAD # 40


msg = log.msg

def err(f=None, **kwargs):
    if not f:
        f = failure.Failure()
    kwargs['failure'] = f
    if 'level' not in kwargs:
        kwargs['level'] = log.UNUSUAL
    return log.msg("failure", **kwargs)
