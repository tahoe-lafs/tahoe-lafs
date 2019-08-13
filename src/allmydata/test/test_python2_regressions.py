"""
Tests to check for Python2 regressions
"""

from types import (
    ClassType,
)

from twisted.trial import unittest
from twisted.python.modules import getModule

def is_classic_class(obj):
    """check an object being a classic class"""
    # issubclass() is a great idea but it blows up if the first argument is
    # not a class.  So ... less than completely useful.
    return type(obj) is ClassType

class PythonTwoRegressions(unittest.TestCase):
    """
    A test class to hold Python2 regression tests.
    """
    def test_new_style_class(self):
        """
        All classes defined by Tahoe-LAFS are new-style.
        """
        for mod in getModule("allmydata").walkModules():
            # Cannot iterate attributes of unloaded modules.
            mod.load()
            for attr in mod.iterAttributes():
                value = attr.load()
                self.assertFalse(
                    is_classic_class(value),
                    "{} appears to be a classic class".format(attr.name),
                )
