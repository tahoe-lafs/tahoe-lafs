"""
Tools to mess with dicts.
"""

import operator
from bisect import bisect_left, insort_left

from allmydata.util.assertutil import _assert, precondition

def move(k, d1, d2, strict=False):
    """
    Move item with key k from d1 to d2.
    """
    if strict and not d1.has_key(k):
        raise KeyError, k

    d2[k] = d1[k]
    del d1[k]

def subtract(d1, d2):
    """
    Remove all items from d1 whose key occurs in d2.

    @returns d1
    """
    if len(d1) > len(d2):
        for k in d2.keys():
            if d1.has_key(k):
                del d1[k]
    else:
        for k in d1.keys():
            if d2.has_key(k):
                del d1[k]
    return d1

class DictOfSets(dict):
    def add(self, key, value):
        if key in self:
            self[key].add(value)
        else:
            self[key] = set([value])

    def update(self, otherdictofsets):
        for key, values in otherdictofsets.iteritems():
            if key in self:
                self[key].update(values)
            else:
                self[key] = set(values)

    def discard(self, key, value):
        if not key in self:
            return
        self[key].discard(value)
        if not self[key]:
            del self[key]

def del_if_present(d, k):
    if d.has_key(k):
        del d[k]

class ValueOrderedDict:
    """
    Note: this implementation assumes that the values do not mutate and change
    their sort order.  That is, it stores the values in a sorted list and
    as items are added and removed from the dict, it makes updates to the list
    which will keep the list sorted.  But if a value that is currently sitting
    in the list changes its sort order, then the internal consistency of this
    object will be lost.

    If that happens, and if assertion checking is turned on, then you will get
    an assertion failure the very next time you try to do anything with this
    ValueOrderedDict.  However, those internal consistency checks are very slow
    and almost certainly unacceptable to leave turned on in production code.
    """
    class ItemIterator:
        def __init__(self, c):
            self.c = c
            self.i = 0
        def __iter__(self):
            return self
        def next(self):
            precondition(self.i <= len(self.c.l), "The iterated ValueOrderedDict doesn't have this many elements.  Most likely this is because someone altered the contents of the ValueOrderedDict while the iteration was in progress.", self.i, self.c)
            precondition((self.i == len(self.c.l)) or self.c.d.has_key(self.c.l[self.i][1]), "The iterated ValueOrderedDict doesn't have this key.  Most likely this is because someone altered the contents of the ValueOrderedDict while the iteration was in progress.", self.i, (self.i < len(self.c.l)) and self.c.l[self.i], self.c)
            if self.i == len(self.c.l):
                raise StopIteration
            le = self.c.l[self.i]
            self.i += 1
            return (le[1], le[0],)

    def iteritems(self):
        return ValueOrderedDict.ItemIterator(self)

    def items(self):
        return zip(map(operator.__getitem__, self.l, [1]*len(self.l)), map(operator.__getitem__, self.l, [0]*len(self.l)))

    def values(self):
        return map(operator.__getitem__, self.l, [0]*len(self.l))

    def keys(self):
        return map(operator.__getitem__, self.l, [1]*len(self.l))

    class KeyIterator:
        def __init__(self, c):
            self.c = c
            self.i = 0
        def __iter__(self):
            return self
        def next(self):
            precondition(self.i <= len(self.c.l), "The iterated ValueOrderedDict doesn't have this many elements.  Most likely this is because someone altered the contents of the ValueOrderedDict while the iteration was in progress.", self.i, self.c)
            precondition((self.i == len(self.c.l)) or self.c.d.has_key(self.c.l[self.i][1]), "The iterated ValueOrderedDict doesn't have this key.  Most likely this is because someone altered the contents of the ValueOrderedDict while the iteration was in progress.", self.i, (self.i < len(self.c.l)) and self.c.l[self.i], self.c)
            if self.i == len(self.c.l):
                raise StopIteration
            le = self.c.l[self.i]
            self.i += 1
            return le[1]

    def iterkeys(self):
        return ValueOrderedDict.KeyIterator(self)

    class ValueIterator:
        def __init__(self, c):
            self.c = c
            self.i = 0
        def __iter__(self):
            return self
        def next(self):
            precondition(self.i <= len(self.c.l), "The iterated ValueOrderedDict doesn't have this many elements.  Most likely this is because someone altered the contents of the ValueOrderedDict while the iteration was in progress.", self.i, self.c)
            precondition((self.i == len(self.c.l)) or self.c.d.has_key(self.c.l[self.i][1]), "The iterated ValueOrderedDict doesn't have this key.  Most likely this is because someone altered the contents of the ValueOrderedDict while the iteration was in progress.", self.i, (self.i < len(self.c.l)) and self.c.l[self.i], self.c)
            if self.i == len(self.c.l):
                raise StopIteration
            le = self.c.l[self.i]
            self.i += 1
            return le[0]

    def itervalues(self):
        return ValueOrderedDict.ValueIterator(self)

    def __init__(self, initialdata={}):
        self.d = {} # k: key, v: val
        self.l = [] # sorted list of tuples of (val, key,)
        self.update(initialdata)
        assert self._assert_invariants()

    def __len__(self):
        return len(self.l)

    def __repr_n__(self, n=None):
        s = ["{",]
        try:
            iter = self.iteritems()
            x = iter.next()
            s.append(str(x[0])); s.append(": "); s.append(str(x[1]))
            i = 1
            while (n is None) or (i < n):
                i += 1
                x = iter.next()
                s.append(", ");
                s.append(str(x[0])); s.append(": "); s.append(str(x[1]))
            # if we get here, we're truncating the repr, so make that clear
            s.append(", ...")
        except StopIteration:
            pass
        s.append("}")
        return ''.join(s)

    def __repr__(self):
        return "<%s %s>" % (self.__class__.__name__, self.__repr_n__(),)

    def __str__(self):
        return "<%s %s>" % (self.__class__.__name__, self.__repr_n__(16),)

    def __eq__(self, other):
        for (k, v,) in other.iteritems():
            if not self.d.has_key(k) or self.d[k] != v:
                return False
        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    def _assert_invariants(self):
        iter = self.l.__iter__()
        try:
            oldx = iter.next()
            while True:
                x = iter.next()
                # self.l is required to be sorted
                _assert(x >= oldx, x, oldx)
                # every element of self.l is required to appear in self.d
                _assert(self.d.has_key(x[1]), x)
                oldx =x
        except StopIteration:
            pass
        for (k, v,) in self.d.iteritems():
            i = bisect_left(self.l, (v, k,))
            while (self.l[i][0] is not v) or (self.l[i][1] is not k):
                i += 1
            _assert(i < len(self.l), i, len(self.l), k, v, self.l)
            _assert(self.l[i][0] is v, i, v, l=self.l, d=self.d)
            _assert(self.l[i][1] is k, i, k, l=self.l, d=self.d)
        return True

    def insert(self, key, val=None):
        assert self._assert_invariants()
        result = self.__setitem__(key, val)
        assert self._assert_invariants()
        return result

    def setdefault(self, key, default=None):
        assert self._assert_invariants()
        if not self.has_key(key):
            self[key] = default
        assert self._assert_invariants()
        return self[key]

    def __setitem__(self, key, val=None):
        assert self._assert_invariants()
        if self.d.has_key(key):
            oldval = self.d[key]
            if oldval != val:
                # re-sort
                i = bisect_left(self.l, (oldval, key,))
                while (self.l[i][0] is not oldval) or (self.l[i][1] is not key):
                    i += 1
                self.l.pop(i)
                insort_left(self.l, (val, key,))
            elif oldval is not val:
                # replace
                i = bisect_left(self.l, (oldval, key,))
                while (self.l[i][0] is not oldval) or (self.l[i][1] is not key):
                    i += 1
                self.l[i] = (val, key,)
        else:
            insort_left(self.l, (val, key,))

        self.d[key] = val
        assert self._assert_invariants()
        return val

    def remove(self, key, default=None, strictkey=True):
        assert self._assert_invariants()
        result = self.__delitem__(key, default, strictkey)
        assert self._assert_invariants()
        return result

    def __getitem__(self, key, default=None, strictkey=True):
        if not self.d.has_key(key):
            if strictkey:
                raise KeyError, key
            else:
                return default
        return self.d[key]

    def __delitem__(self, key, default=None, strictkey=True):
        """
        @param strictkey: True if you want a KeyError in the case that
            key is not there, False if you want a reference to default
            in the case that key is not there
        @param default: the object to return if key is not there; This
            is ignored if strictkey.

        @return: the object removed or default if there is not item by
            that key and strictkey is False
        """
        assert self._assert_invariants()
        if self.d.has_key(key):
            val = self.d.pop(key)
            i = bisect_left(self.l, (val, key,))
            while (self.l[i][0] is not val) or (self.l[i][1] is not key):
                i += 1
            self.l.pop(i)
            assert self._assert_invariants()
            return val
        elif strictkey:
            assert self._assert_invariants()
            raise KeyError, key
        else:
            assert self._assert_invariants()
            return default

    def clear(self):
        assert self._assert_invariants()
        self.d.clear()
        del self.l[:]
        assert self._assert_invariants()

    def update(self, otherdict):
        """
        @return: self
        """
        assert self._assert_invariants()
        for (k, v,) in otherdict.iteritems():
            self.insert(k, v)
        assert self._assert_invariants()
        return self

    def has_key(self, key):
        assert self._assert_invariants()
        return self.d.has_key(key)

    def popitem(self):
        if not self.l:
            raise KeyError, 'popitem(): dictionary is empty'
        le = self.l.pop(0)
        del self.d[le[1]]
        return (le[1], le[0],)

    def pop(self, k, default=None, strictkey=False):
        if not self.d.has_key(k):
            if strictkey:
                raise KeyError, k
            else:
                return default
        v = self.d.pop(k)
        i = bisect_left(self.l, (v, k,))
        while (self.l[i][0] is not v) or (self.l[i][1] is not k):
            i += 1
        self.l.pop(i)
        return v

    def pop_from_list(self, i=0):
        le = self.l.pop(i)
        del self.d[le[1]]
        return le[1]

class AuxValueDict(dict):
    """I behave like a regular dict, but each key is associated with two
    values: the main value, and an auxilliary one. Setting the main value
    (with the usual d[key]=value) clears the auxvalue. You can set both main
    and auxvalue at the same time, and can retrieve the values separately.

    The main use case is a dictionary that represents unpacked child values
    for a directory node, where a common pattern is to modify one or more
    children and then pass the dict back to a packing function. The original
    packed representation can be cached in the auxvalue, and the packing
    function can use it directly on all unmodified children. On large
    directories with a complex packing function, this can save considerable
    time."""

    def __init__(self, *args, **kwargs):
        super(AuxValueDict, self).__init__(*args, **kwargs)
        self.auxilliary = {}

    def __setitem__(self, key, value):
        super(AuxValueDict, self).__setitem__(key, value)
        self.auxilliary[key] = None # clear the auxvalue

    def __delitem__(self, key):
        super(AuxValueDict, self).__delitem__(key)
        self.auxilliary.pop(key)

    def get_aux(self, key, default=None):
        """Retrieve the auxilliary value. There is no way to distinguish
        between an auxvalue of 'None' and a key that does not have an
        auxvalue, and get_aux() will not raise KeyError when called with a
        missing key."""
        return self.auxilliary.get(key, default)

    def set_with_aux(self, key, value, auxilliary):
        """Set both the main value and the auxilliary value. There is no way
        to distinguish between an auxvalue of 'None' and a key that does not
        have an auxvalue."""
        super(AuxValueDict, self).__setitem__(key, value)
        self.auxilliary[key] = auxilliary
