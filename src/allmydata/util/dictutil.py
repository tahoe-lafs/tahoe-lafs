"""
Tools to mess with dicts.
"""

from __future__ import annotations
from typing import Callable, TypeVar

K = TypeVar("K")
V = TypeVar("V")

def filter(pred: Callable[[V], bool], orig: dict[K, V]) -> dict[K, V]:
    """
    Filter out key/value pairs whose value fails to match a predicate.
    """
    return {
        k: v
        for (k, v)
        in orig.items()
        if pred(v)
    }

class DictOfSets(dict):
    def add(self, key, value):
        if key in self:
            self[key].add(value)
        else:
            self[key] = set([value])

    def update(self, otherdictofsets):
        for key, values in list(otherdictofsets.items()):
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


class _TypedKeyDict(dict):
    """Dictionary that enforces key type.

    Doesn't override everything, but probably good enough to catch most
    problems.

    Subclass and override KEY_TYPE.
    """

    KEY_TYPE = object

    def __init__(self, *args, **kwargs):
        dict.__init__(self, *args, **kwargs)
        for key in self:
            if not isinstance(key, self.KEY_TYPE):
                raise TypeError("{} must be of type {}".format(
                    repr(key), self.KEY_TYPE))


def _make_enforcing_override(K, method_name):
    def f(self, key, *args, **kwargs):
        if not isinstance(key, self.KEY_TYPE):
            raise TypeError("{} must be of type {}".format(
                repr(key), self.KEY_TYPE))
        return getattr(dict, method_name)(self, key, *args, **kwargs)
    f.__name__ = method_name
    setattr(K, method_name, f)

for _method_name in ["__setitem__", "__getitem__", "setdefault", "get",
                     "__delitem__"]:
    _make_enforcing_override(_TypedKeyDict, _method_name)
del _method_name


class BytesKeyDict(_TypedKeyDict):
    """Keys should be bytes."""

    KEY_TYPE = bytes


class UnicodeKeyDict(_TypedKeyDict):
    """Keys should be unicode strings."""

    KEY_TYPE = str
