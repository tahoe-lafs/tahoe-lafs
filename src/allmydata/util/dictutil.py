"""
Tools to mess with dicts.
"""

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
