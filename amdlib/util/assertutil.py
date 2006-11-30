# Copyright (c) 2003-2006 Bryce "Zooko" Wilcox-O'Hearn
# mailto:zooko@zooko.com
# http://zooko.com/
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this work to deal in this work without restriction (including the 
# rights to use, modify, distribute, sublicense, and/or sell copies)

"""
Tests useful in assertion checking, prints out nicely formated messages too.
"""

from humanreadable import hr

from twisted.python import log

def _assert(___cond=False, *___args, **___kwargs):
    if ___cond:
        return True
    msgbuf=[]
    if ___args:
        msgbuf.append("%s %s" % tuple(map(hr, (___args[0], type(___args[0]),))))
        msgbuf.extend([", %s %s" % tuple(map(hr, (arg, type(arg),))) for arg in ___args[1:]])
        if ___kwargs:
            msgbuf.append(", %s: %s %s" % ((___kwargs.items()[0][0],) + tuple(map(hr, (___kwargs.items()[0][1], type(___kwargs.items()[0][1]),)))))
    else:
        if ___kwargs:
            msgbuf.append("%s: %s %s" % ((___kwargs.items()[0][0],) + tuple(map(hr, (___kwargs.items()[0][1], type(___kwargs.items()[0][1]),)))))
    msgbuf.extend([", %s: %s %s" % tuple(map(hr, (k, v, type(v),))) for k, v in ___kwargs.items()[1:]])

    raise AssertionError, "".join(msgbuf)

    return False

def precondition(___cond=False, *___args, **___kwargs):
    try:
        if ___cond:
            return True
        msgbuf=["precondition", ]
        if ___args or ___kwargs:
            msgbuf.append(": ")
        if ___args:
            msgbuf.append("%s %s" % tuple(map(hr, (___args[0], type(___args[0]),))))
            msgbuf.extend([", %s %s" % tuple(map(hr, (arg, type(arg),))) for arg in ___args[1:]])
            if ___kwargs:
                msgbuf.append(", %s: %s %s" % ((___kwargs.items()[0][0],) + tuple(map(hr, (___kwargs.items()[0][1], type(___kwargs.items()[0][1]),)))))
        else:
            if ___kwargs:
                msgbuf.append("%s: %s %s" % ((___kwargs.items()[0][0],) + tuple(map(hr, (___kwargs.items()[0][1], type(___kwargs.items()[0][1]),)))))
        msgbuf.extend([", %s: %s %s" % tuple(map(hr, (k, v, type(v),))) for k, v in ___kwargs.items()[1:]])
    except Exception, le:
        log.msg("assertutil.precondition(): INTERNAL ERROR IN pyutil.assertutil. %s %s %s" % (type(le), repr(le), le.args,))
        log.err()
        raise le
    except:
        log.msg("assertutil.precondition(): INTERNAL ERROR IN pyutil.assertutil.")
        log.err()
        raise

    raise AssertionError, "".join(msgbuf)

    return False

def postcondition(___cond=False, *___args, **___kwargs):
    if ___cond:
        return True
    msgbuf=["postcondition", ]
    if ___args or ___kwargs:
        msgbuf.append(": ")
    if ___args:
        msgbuf.append("%s %s" % tuple(map(hr, (___args[0], type(___args[0]),))))
        msgbuf.extend([", %s %s" % tuple(map(hr, (arg, type(arg),))) for arg in ___args[1:]])
        if ___kwargs:
            msgbuf.append(", %s: %s %s" % ((___kwargs.items()[0][0],) + tuple(map(hr, (___kwargs.items()[0][1], type(___kwargs.items()[0][1]),)))))
    else:
        if ___kwargs:
            msgbuf.append("%s: %s %s" % ((___kwargs.items()[0][0],) + tuple(map(hr, (___kwargs.items()[0][1], type(___kwargs.items()[0][1]),)))))
    msgbuf.extend([", %s: %s %s" % tuple(map(hr, (k, v, type(v),))) for k, v in ___kwargs.items()[1:]])

    raise AssertionError, "".join(msgbuf)

    return False

