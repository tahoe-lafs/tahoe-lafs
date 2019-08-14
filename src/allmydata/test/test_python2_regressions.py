"""
Tests to check for Python2 regressions
"""

from inspect import isclass

from twisted.python.modules import getModule

from testtools import (
    TestCase,
)
from testtools.matchers import (
    Equals,
)

BLACKLIST = {
    "allmydata.test.check_load",
    "allmydata.watchdog._watchdog_541",
    "allmydata.watchdog.inotify",
    "allmydata.windows.inotify",
    "allmydata.windows.registry",
    "allmydata.windows.tahoesvc",
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
