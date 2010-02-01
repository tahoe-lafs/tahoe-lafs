from twisted.trial import unittest

from formless import webform

class Web(unittest.TestCase):
    def test_read_default_css(self):
        """
        Sometimes Nevow can't find its resource files such as its default css file.
        """
        import pkg_resources
        try:
            pkg_resources.require("Nevow>=0.9.33")
        except pkg_resources.VersionConflict:
            raise unittest.SkipTest("We pass this test only with Nevow >= v0.9.33, which is the first version of Nevow that has our patch from http://www.divmod.org/trac/ticket/2527")
        webform.defaultCSS.openForReading()
