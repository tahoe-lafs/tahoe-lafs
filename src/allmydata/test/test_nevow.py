from twisted.trial import unittest

from formless import webform

class Web(unittest.TestCase):
    def test_read_default_css(self):
        """
        Sometimes Nevow can't find its resource files such as its default css file.
        """

        from allmydata import get_package_versions, normalized_version
        nevow_ver = get_package_versions()['Nevow']

        if not normalized_version(nevow_ver) >= normalized_version('0.9.33'):
            raise unittest.SkipTest("We pass this test only with Nevow >= v0.9.33, which is the first version of Nevow that has our patch from http://www.divmod.org/trac/ticket/2527")

        webform.defaultCSS.openForReading()
