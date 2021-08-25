"""
Tests to check for Python2 regressions
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from unittest import skipUnless
from inspect import isclass

from twisted.python.modules import getModule

from testtools import (
    TestCase,
)
from testtools.matchers import (
    Equals,
)

BLACKLIST = {
    "allmydata.scripts.types_",
    "allmydata.test._win_subprocess",
    "allmydata.windows.registry",
    "allmydata.windows.fixups",
}


def is_new_style(cls):
    """
    :return bool: ``True`` if and only if the given class is "new style".
    """
    # All new-style classes are instances of type.  By definition.
    return isinstance(cls, type)

def defined_here(cls, where):
    """
    :return bool: ``True`` if and only if the given class was defined in a
        module with the given name.

    :note: Classes can lie about where they are defined.  Try not to do that.
    """
    return cls.__module__ == where


class PythonTwoRegressions(TestCase):
    """
    Regression tests for Python 2 behaviors related to Python 3 porting.
    """
    @skipUnless(PY2, "No point in running on Python 3.")
    def test_new_style_classes(self):
        """
        All classes in Tahoe-LAFS are new-style.
        """
        newstyle = set()
        classic = set()
        for mod in getModule("allmydata").walkModules():
            if mod.name in BLACKLIST:
                continue

            # iterAttributes will only work on loaded modules.  So, load it.
            mod.load()

            for attr in mod.iterAttributes():
                value = attr.load()
                if isclass(value) and defined_here(value, mod.name):
                    if is_new_style(value):
                        newstyle.add(value)
                    else:
                        classic.add(value)

        self.assertThat(
            classic,
            Equals(set()),
            "Expected to find no classic classes.",
        )
