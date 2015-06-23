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
        d = self.do_cli("magic-folder", "create", "magic")
        def _done((rc,stdout,stderr)):
            self.failUnless("Alias 'magic' created" in stdout)
            self.failIf(stderr)
            aliases = get_aliases(self.get_clientdir())
            self.failUnless("magic" in aliases)
            self.failUnless(aliases["magic"].startswith("URI:DIR2:"))
        d.addCallback(_done)
        return d

    def _invite(self, ignore):
        d = self.do_cli("magic-folder", "invite", u"magic", u"Alice")
        return d

    def _join(self, result):
        invite_code = result[1].strip()
        d = self.do_cli("magic-folder", "join", invite_code, u"LOCAL_DIR")
        return d

    def test_create_and_then_invite_join(self):
        self.basedir = "cli/MagicFolder/create-and-then-invite-join"
        self.set_up_grid()
        d = self._create_magic_folder()
        d.addCallback(self._invite)
        d.addCallback(self._join)
        return d

    def test_create_invite_join(self):
        self.basedir = "cli/MagicFolder/create-invite-join"
        self.set_up_grid()
        magic_local_dir = os.path.join(self.basedir, "magic")
        d = self.do_cli("magic-folder", "create", u"magic", u"Alice", magic_local_dir)
        return d
