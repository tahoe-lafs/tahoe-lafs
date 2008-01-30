from twisted.trial import unittest

from formless import webform

class Web(unittest.TestCase):
    def test_read_default_css(self):
        """
        Sometimes Nevow can't find its resource files such as its default css file.
        """
        webform.defaultCSS.openForReading()
    test_read_default_css.todo = "We have a patch for Nevow that makes this test pass, but we haven't decided how to manage a patched version of Nevow, and the Nevow upstream folks haven't decided to accept our patch."
