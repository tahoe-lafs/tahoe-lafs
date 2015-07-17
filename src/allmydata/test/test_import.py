
from twisted.trial import unittest
from twisted.python.monkey import MonkeyPatcher

import allmydata
import __builtin__


class T(unittest.TestCase):
    def test_report_import_error(self):
        real_import_func = __import__
        def raiseIE_from_this_particular_func(name, *args):
            if name == "foolscap":
                marker = "wheeeyo"
                raise ImportError(marker + " foolscap cant be imported")
            else:
                return real_import_func(name, *args)

        # Let's run as little code as possible with __import__ patched.
        patcher = MonkeyPatcher((__builtin__, '__import__', raiseIE_from_this_particular_func))
        vers_and_locs = patcher.runWithPatches(allmydata.get_package_versions_and_locations)

        for (pkgname, stuff) in vers_and_locs:
            if pkgname == 'foolscap':
                self.failUnless('wheeeyo' in str(stuff[2]), stuff)
                self.failUnless('raiseIE_from_this_particular_func' in str(stuff[2]), stuff)
