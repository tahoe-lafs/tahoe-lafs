
from twisted.trial import unittest
from twisted.python.monkey import MonkeyPatcher

import allmydata
import __builtin__


class T(unittest.TestCase):
    def test_report_import_error(self):
        marker = "wheeeyo"
        real_import_func = __import__
        def raiseIE_from_this_particular_func(name, *args):
            if name == "foolscap":
                raise ImportError(marker + " foolscap cant be imported")
            else:
                return real_import_func(name, *args)

        # Let's run as little code as possible with __import__ patched.
        patcher = MonkeyPatcher((__builtin__, '__import__', raiseIE_from_this_particular_func))
        vers_and_locs, errors = patcher.runWithPatches(allmydata.get_package_versions_and_locations)

        foolscap_stuffs = [stuff for (pkg, stuff) in vers_and_locs if pkg == 'foolscap']
        self.failUnlessEqual(len(foolscap_stuffs), 1)
        comment = str(foolscap_stuffs[0][2])
        self.failUnlessIn(marker, comment)
        self.failUnlessIn('raiseIE_from_this_particular_func', comment)

        self.failUnless([e for e in errors if "dependency \'foolscap\' could not be imported" in e])
