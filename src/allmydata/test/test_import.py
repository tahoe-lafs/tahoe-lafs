
from twisted.trial import unittest

import allmydata
import mock

real_import_func = __import__

class T(unittest.TestCase):
    @mock.patch('__builtin__.__import__')
    def test_report_import_error(self, mockimport):
        def raiseIE_from_this_particular_func(name, *args):
            if name == "foolscap":
                marker = "wheeeyo"
                raise ImportError(marker + " foolscap cant be imported")
            else:
                return real_import_func(name, *args)

        mockimport.side_effect = raiseIE_from_this_particular_func

        vers_and_locs =  allmydata.get_package_versions_and_locations()
        for (pkgname, stuff) in vers_and_locs:
            if pkgname == 'foolscap':
                self.failUnless('wheeeyo' in str(stuff[2]), stuff)
                self.failUnless('raiseIE_from_this_particular_func' in str(stuff[2]), stuff)
