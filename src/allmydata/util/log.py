
import itertools
from twisted.python import log

counter = itertools.count()

def msg(*message, **kw):
    if 'number' not in kw:
        number = counter.next()
        kw['number'] = number
    else:
        number = kw['number']
    if 'parent' not in kw:
        kw['parent'] = None
    log.msg(*message, **kw)
    return number

def err(*args, **kw):
    if 'number' not in kw:
        number = counter.next()
        kw['number'] = number
    else:
        number = kw['number']
    if 'parent' not in kw:
        kw['parent'] = None
    log.err(*args, **kw)
    return number
