
from foolscap.logging import log

NOISY = log.NOISY # 10
OPERATIONAL = log.OPERATIONAL # 20
UNUSUAL = log.UNUSUAL # 23
INFREQUENT = log.INFREQUENT # 25
CURIOUS = log.CURIOUS # 28
WEIRD = log.WEIRD # 30
SCARY = log.SCARY # 35
BAD = log.BAD # 40


msg = log.msg

def err(*args, **kwargs):
    # this should probably be in foolscap
    if 'level' not in kwargs:
        kwargs['level'] = log.UNUSUAL
    return log.err(*args, **kwargs)
