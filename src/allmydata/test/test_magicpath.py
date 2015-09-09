
from twisted.trial import unittest

from allmydata import magicpath


class MagicPath(unittest.TestCase):
    tests = {
        u"Documents/work/critical-project/qed.txt": u"Documents@_work@_critical-project@_qed.txt",
        u"Documents/emails/bunnyfufu@hoppingforest.net": u"Documents@_emails@_bunnyfufu@@hoppingforest.net",
        u"foo/@/bar": u"foo@_@@@_bar",
    }

    def test_path2magic(self):
        for test, expected in self.tests.items():
            self.failUnlessEqual(magicpath.path2magic(test), expected)

    def test_magic2path(self):
        for expected, test in self.tests.items():
            self.failUnlessEqual(magicpath.magic2path(test), expected)

    def test_should_ignore(self):
        self.failUnlessEqual(magicpath.should_ignore_file(".bashrc"), True)
        self.failUnlessEqual(magicpath.should_ignore_file("bashrc."), False)
        self.failUnlessEqual(magicpath.should_ignore_file("forest/tree/branch/.bashrc"), True)
        self.failUnlessEqual(magicpath.should_ignore_file("forest/tree/.branch/bashrc"), True)
        self.failUnlessEqual(magicpath.should_ignore_file("forest/.tree/branch/bashrc"), True)
        self.failUnlessEqual(magicpath.should_ignore_file("forest/tree/branch/bashrc"), False)
