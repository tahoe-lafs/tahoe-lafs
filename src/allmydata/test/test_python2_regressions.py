"""
Tests to check for Python2 regressions
"""
from twisted.trial import unittest
from twisted.python.modules import getModule

class PythonTwoRegressions(unittest.TestCase):
    """
    A test class to hold Python2 regression tests.
    """

    def is_new_style(self, cls):
        """check for being a new-style class"""
        # another test could be: issubclass(value, type)
        has_class_attr = hasattr(cls, '__class__')
        dict_or_slots = '__dict__' in dir(cls) or hasattr(cls, '__slots__')
        return has_class_attr and dict_or_slots

    def test_old_style_class(self):
        """
        Check if all classes are new-style classes
        """
        for mod in getModule("allmydata").walkModules():
            for attr in mod.iterAttributes():
                value = attr.load()
                if isinstance(value, str):
                    # apparently strings are note a new-style class (in Python 2.7)
                    # so we skip testing them
                    return
                self.assertTrue(self.is_new_style(value),
                                "{} does not seem to be a new-style class".format(attr.name))
