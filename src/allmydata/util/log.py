
from foolscap.logging import log
from twisted.python import failure

msg = log.msg

def err(f=None, **kwargs):
    if not f:
        f = failure.Failure()
    kwargs['failure'] = f
    if 'level' not in kwargs:
        kwargs['level'] = log.UNUSUAL
    return log.msg("failure", **kwargs)
