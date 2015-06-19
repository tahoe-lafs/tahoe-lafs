import os.path
import urllib

from twisted.trial import unittest

from allmydata.util import fileutil
from allmydata.scripts.common import get_aliases
from allmydata.scripts import cli, runner
from allmydata.test.no_network import GridTestMixin
from allmydata.util.encodingutil import quote_output, get_io_encoding
from .test_cli import CLITestMixin


class CreateMagicFolder(GridTestMixin, CLITestMixin, unittest.TestCase):

    def _create_magic_folder(self):
        d = self.do_cli("magic-folder", "create", "my_magic_folder")
        def _done((rc,stdout,stderr)):
            self.failUnless("Alias 'my_magic_folder' created" in stdout)
            self.failIf(stderr)
            aliases = get_aliases(self.get_clientdir())
            self.failUnless("my_magic_folder" in aliases)
            self.failUnless(aliases["my_magic_folder"].startswith("URI:DIR2:"))
        d.addCallback(_done)
        return d

    def test_create(self):
        self.basedir = "cli/MagicFolder/create"
        self.set_up_grid()
        return self._create_magic_folder()

    def _invite(self, ignore):
        d = self.do_cli("magic-folder", "invite", "magicFolder1", "Nicki")
        return d

    def test_invite(self):
        self.basedir = "cli/MagicFolder/invite"
        self.set_up_grid()
        d = self._create_magic_folder()
        d.addCallback(self._invite)
        return d
