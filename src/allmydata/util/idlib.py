from base64 import b32encode, b32decode

def b2a(i):
    assert isinstance(i, str), "tried to idlib.b2a non-string '%s'" % (i,)
    return b32encode(i).lower()

def a2b(i):
    assert isinstance(i, str), "tried to idlib.a2b non-string '%s'" % (i,)
    try:
        return b32decode(i.upper())
    except TypeError:
        print "b32decode failed on a %s byte string '%s'" % (len(i), i)
        raise

