
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
