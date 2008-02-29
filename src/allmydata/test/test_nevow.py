from twisted.trial import unittest

from formless import webform

class Web(unittest.TestCase):
    def test_read_default_css(self):
        """
        Sometimes Nevow can't find its resource files such as its default css file.
        """
        webform.defaultCSS.openForReading()
    test_read_default_css.todo = "This patch that we submitted to Nevow fixes this issue: http://www.divmod.org/trac/ticket/2527"
