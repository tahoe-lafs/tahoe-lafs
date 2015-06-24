import os.path
import urllib
import re
import json

from twisted.trial import unittest

from allmydata.util import fileutil
from allmydata.scripts.common import get_aliases
from allmydata.scripts import cli, runner
from allmydata.test.no_network import GridTestMixin
from allmydata.util.encodingutil import quote_output, get_io_encoding
from .test_cli import CLITestMixin
from allmydata.scripts import magic_folder_cli

class CreateMagicFolder(GridTestMixin, CLITestMixin, unittest.TestCase):

    def _create_magic_folder(self):
        d = self.do_cli("magic-folder", "create", "magic")
        def _done((rc,stdout,stderr)):
            self.failUnless(rc == 0)
            self.failUnless("Alias 'magic' created" in stdout)
            self.failIf(stderr)
            aliases = get_aliases(self.get_clientdir())
            self.failUnless("magic" in aliases)
            self.failUnless(aliases["magic"].startswith("URI:DIR2:"))
        d.addCallback(_done)
        return d

    def _invite(self, ignore):
        d = self.do_cli("magic-folder", "invite", u"magic", u"Alice")
        def _done((rc,stdout,stderr)):
            self.failUnless(rc == 0)
            return (rc,stdout,stderr)
        d.addCallback(_done)
        return d

    def _diminish(self, write_cap):
        d = self.do_cli("ls", "--json", write_cap)
        def get_readonly_cap((rc,stdout,stderr)):
            self.failUnless(rc == 0)
            readonly_cap = json.loads(stdout)[1][u"ro_uri"]
            return readonly_cap
        d.addCallback(get_readonly_cap)
        return d

    def _try_joined_config(self, result):
        collective_readonly_cap = fileutil.read(os.path.join(self.get_clientdir(), "private/collective_dircap"))
        d = self.do_cli("ls", "--json", collective_readonly_cap)
        def _done((rc,stdout,stderr)):
            self.failUnless(rc == 0)
            return (rc,stdout,stderr)
        d.addCallback(_done)
        def test_joined_magic_folder((rc,stdout,stderr)):
            d2 = self._diminish(self.dmd_write_cap)
            def fail_unless_dmd_readonly_exists(readonly_cap):
                s = re.search(readonly_cap, stdout)
                self.failUnless(s is not None)
            d2.addCallback(fail_unless_dmd_readonly_exists)
            return d2
        d.addCallback(test_joined_magic_folder)
        return d

    def _get_caps_from_files(self, result):
        self.magic_readonly_cap = fileutil.read(os.path.join(self.get_clientdir(), "private/collective_dircap"))
        self.dmd_write_cap = fileutil.read(os.path.join(self.get_clientdir(), "private/magic_folder_dircap"))
        self.failIf(self.magic_readonly_cap is None or self.dmd_write_cap is None)

    def _join(self, result):
        invite_code = result[1].strip()
        self.magic_readonly_cap, self.dmd_write_cap = invite_code.split(magic_folder_cli.INVITE_SEPERATOR)
        d = self.do_cli("magic-folder", "join", invite_code, self.magic_local_dir)
        def _done((rc,stdout,stderr)):
            self.failUnless(rc == 0)
            return (rc,stdout,stderr)
        d.addCallback(_done)
        return d

    def _check_config(self, result):
        client_config = fileutil.read(os.path.join(self.get_clientdir(), "tahoe.cfg"))
        ret = re.search("\[magic_folder\]\nenabled = True\nlocal.directory = %s" % (self.magic_local_dir,), client_config)
        self.failIf(ret is None)
        return result

    def test_create_and_then_invite_join(self):
        self.basedir = "cli/MagicFolder/create-and-then-invite-join"
        self.set_up_grid()
        self.magic_local_dir = os.path.join(self.basedir, "magic")

        d = self._create_magic_folder()
        d.addCallback(self._invite)
        d.addCallback(self._join)
        d.addCallback(self._try_joined_config)
        d.addCallback(self._check_config)
        return d

    def test_create_invite_join(self):
        self.basedir = "cli/MagicFolder/create-invite-join"
        self.set_up_grid()
        self.magic_local_dir = os.path.join(self.basedir, "magic")
        d = self.do_cli("magic-folder", "create", u"magic", u"Alice", self.magic_local_dir)
        def _done((rc,stdout,stderr)):
            self.failUnless(rc == 0)
            return (rc,stdout,stderr)
        d.addCallback(_done)
        d.addCallback(self._get_caps_from_files)
        d.addCallback(self._try_joined_config)
        d.addCallback(self._check_config)
        return d
