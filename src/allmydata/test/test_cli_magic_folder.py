import os.path
import re

from twisted.trial import unittest
from twisted.internet import defer
from twisted.internet import reactor
from twisted.python import usage

from allmydata.util.assertutil import precondition
from allmydata.util import fileutil
from allmydata.scripts.common import get_aliases
from allmydata.test.no_network import GridTestMixin
from .test_cli import CLITestMixin
from allmydata.scripts import magic_folder_cli
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.util.encodingutil import unicode_to_argv
from allmydata.frontends.magic_folder import MagicFolder
from allmydata import uri


class MagicFolderCLITestMixin(CLITestMixin, GridTestMixin):
    def do_create_magic_folder(self, client_num):
        d = self.do_cli("magic-folder", "create", "magic:", client_num=client_num)
        def _done((rc,stdout,stderr)):
            self.failUnlessEqual(rc, 0)
            self.failUnlessIn("Alias 'magic' created", stdout)
            self.failUnlessEqual(stderr, "")
            aliases = get_aliases(self.get_clientdir(i=client_num))
            self.failUnlessIn("magic", aliases)
            self.failUnless(aliases["magic"].startswith("URI:DIR2:"))
        d.addCallback(_done)
        return d

    def do_invite(self, client_num, nickname):
        nickname_arg = unicode_to_argv(nickname)
        d = self.do_cli("magic-folder", "invite", "magic:", nickname_arg, client_num=client_num)
        def _done((rc, stdout, stderr)):
            self.failUnlessEqual(rc, 0)
            return (rc, stdout, stderr)
        d.addCallback(_done)
        return d

    def do_join(self, client_num, local_dir, invite_code):
        precondition(isinstance(local_dir, unicode), local_dir=local_dir)
        precondition(isinstance(invite_code, str), invite_code=invite_code)

        local_dir_arg = unicode_to_argv(local_dir)
        d = self.do_cli("magic-folder", "join", invite_code, local_dir_arg, client_num=client_num)
        def _done((rc, stdout, stderr)):
            self.failUnlessEqual(rc, 0)
            return (rc, stdout, stderr)
        d.addCallback(_done)
        return d

    def check_joined_config(self, client_num, upload_dircap):
        """Tests that our collective directory has the readonly cap of
        our upload directory.
        """
        collective_readonly_cap = fileutil.read(os.path.join(self.get_clientdir(i=client_num),
                                                             u"private", u"collective_dircap"))
        d = self.do_cli("ls", "--json", collective_readonly_cap, client_num=client_num)
        def _done((rc, stdout, stderr)):
            self.failUnlessEqual(rc, 0)
            return (rc, stdout, stderr)
        d.addCallback(_done)
        def test_joined_magic_folder((rc,stdout,stderr)):
            readonly_cap = unicode(uri.from_string(upload_dircap).get_readonly().to_string(), 'utf-8')
            s = re.search(readonly_cap, stdout)
            self.failUnless(s is not None)
            return None
        d.addCallback(test_joined_magic_folder)
        return d

    def get_caps_from_files(self, client_num):
        collective_dircap = fileutil.read(os.path.join(self.get_clientdir(i=client_num),
                                                       u"private", u"collective_dircap"))
        upload_dircap = fileutil.read(os.path.join(self.get_clientdir(i=client_num),
                                                   u"private", u"magic_folder_dircap"))
        self.failIf(collective_dircap is None or upload_dircap is None)
        return collective_dircap, upload_dircap

    def check_config(self, client_num, local_dir):
        client_config = fileutil.read(os.path.join(self.get_clientdir(i=client_num), "tahoe.cfg"))
        local_dir_utf8 = local_dir.encode('utf-8')
        magic_folder_config = "[magic_folder]\nenabled = True\nlocal.directory = %s" % (local_dir_utf8,)
        self.failUnlessIn(magic_folder_config, client_config)

    def create_invite_join_magic_folder(self, nickname, local_dir):
        nickname_arg = unicode_to_argv(nickname)
        local_dir_arg = unicode_to_argv(local_dir)
        d = self.do_cli("magic-folder", "create", "magic:", nickname_arg, local_dir_arg)
        def _done((rc, stdout, stderr)):
            self.failUnlessEqual(rc, 0)

            client = self.get_client()
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
            self.collective_dirnode = client.create_node_from_uri(self.collective_dircap)
            self.upload_dirnode     = client.create_node_from_uri(self.upload_dircap)
        d.addCallback(_done)
        d.addCallback(lambda ign: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, local_dir))
        return d

    def cleanup(self, res):
        d = defer.succeed(None)
        if self.magicfolder is not None:
            d.addCallback(lambda ign: self.magicfolder.finish())
        d.addCallback(lambda ign: res)
        return d

    def init_magicfolder(self, client_num, upload_dircap, collective_dircap, local_magic_dir, clock):
        dbfile = abspath_expanduser_unicode(u"magicfolderdb.sqlite", base=self.get_clientdir(i=client_num))
        magicfolder = MagicFolder(self.get_client(client_num), upload_dircap, collective_dircap, local_magic_dir,
                                       dbfile, pending_delay=0.2, clock=clock)
        magicfolder.downloader._turn_delay = 0

        magicfolder.setServiceParent(self.get_client(client_num))
        magicfolder.ready()
        return magicfolder

    def setup_alice_and_bob(self, alice_clock=reactor, bob_clock=reactor):
        self.set_up_grid(num_clients=2)

        self.alice_magicfolder = None
        self.bob_magicfolder = None

        alice_magic_dir = abspath_expanduser_unicode(u"Alice-magic", base=self.basedir)
        self.mkdir_nonascii(alice_magic_dir)
        bob_magic_dir = abspath_expanduser_unicode(u"Bob-magic", base=self.basedir)
        self.mkdir_nonascii(bob_magic_dir)

        # Alice creates a Magic Folder,
        # invites herself then and joins.
        d = self.do_create_magic_folder(0)
        d.addCallback(lambda ign: self.do_invite(0, u"Alice\u00F8"))
        def get_invite_code(result):
            self.invite_code = result[1].strip()
        d.addCallback(get_invite_code)
        d.addCallback(lambda ign: self.do_join(0, alice_magic_dir, self.invite_code))
        def get_alice_caps(ign):
            self.alice_collective_dircap, self.alice_upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_alice_caps)
        d.addCallback(lambda ign: self.check_joined_config(0, self.alice_upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, alice_magic_dir))
        def get_Alice_magicfolder(result):
            self.alice_magicfolder = self.init_magicfolder(0, self.alice_upload_dircap,
                                                           self.alice_collective_dircap,
                                                           alice_magic_dir, alice_clock)
            return result
        d.addCallback(get_Alice_magicfolder)

        # Alice invites Bob. Bob joins.
        d.addCallback(lambda ign: self.do_invite(0, u"Bob\u00F8"))
        def get_invite_code(result):
            self.invite_code = result[1].strip()
        d.addCallback(get_invite_code)
        d.addCallback(lambda ign: self.do_join(1, bob_magic_dir, self.invite_code))
        def get_bob_caps(ign):
            self.bob_collective_dircap, self.bob_upload_dircap = self.get_caps_from_files(1)
        d.addCallback(get_bob_caps)
        d.addCallback(lambda ign: self.check_joined_config(1, self.bob_upload_dircap))
        d.addCallback(lambda ign: self.check_config(1, bob_magic_dir))
        def get_Bob_magicfolder(result):
            self.bob_magicfolder = self.init_magicfolder(1, self.bob_upload_dircap,
                                                         self.bob_collective_dircap,
                                                         bob_magic_dir, bob_clock)
            return result
        d.addCallback(get_Bob_magicfolder)
        return d


class CreateMagicFolder(MagicFolderCLITestMixin, unittest.TestCase):
    def test_create_and_then_invite_join(self):
        self.basedir = "cli/MagicFolder/create-and-then-invite-join"
        self.set_up_grid()
        local_dir = os.path.join(self.basedir, "magic")
        abs_local_dir_u = abspath_expanduser_unicode(unicode(local_dir), long_path=False)

        d = self.do_create_magic_folder(0)
        d.addCallback(lambda ign: self.do_invite(0, u"Alice"))
        def get_invite_code_and_join((rc, stdout, stderr)):
            invite_code = stdout.strip()
            return self.do_join(0, unicode(local_dir), invite_code)
        d.addCallback(get_invite_code_and_join)
        def get_caps(ign):
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_caps)
        d.addCallback(lambda ign: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, abs_local_dir_u))
        return d

    def test_create_error(self):
        self.basedir = "cli/MagicFolder/create-error"
        self.set_up_grid()

        d = self.do_cli("magic-folder", "create", "m a g i c:", client_num=0)
        def _done((rc, stdout, stderr)):
            self.failIfEqual(rc, 0)
            self.failUnlessIn("Alias names cannot contain spaces.", stderr)
        d.addCallback(_done)
        return d

    def test_create_invite_join(self):
        self.basedir = "cli/MagicFolder/create-invite-join"
        self.set_up_grid()
        local_dir = os.path.join(self.basedir, "magic")
        abs_local_dir_u = abspath_expanduser_unicode(unicode(local_dir), long_path=False)

        d = self.do_cli("magic-folder", "create", "magic:", "Alice", local_dir)
        def _done((rc, stdout, stderr)):
            self.failUnlessEqual(rc, 0)
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
        d.addCallback(_done)
        d.addCallback(lambda ign: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, abs_local_dir_u))
        return d

    def test_create_invite_join_failure(self):
        self.basedir = "cli/MagicFolder/create-invite-join-failure"
        os.makedirs(self.basedir)

        o = magic_folder_cli.CreateOptions()
        o.parent = magic_folder_cli.MagicFolderCommand()
        o.parent['node-directory'] = self.basedir
        try:
            o.parseArgs("magic:", "Alice", "-foo")
        except usage.UsageError as e:
            self.failUnlessIn("cannot start with '-'", str(e))
        else:
            self.fail("expected UsageError")

    def test_join_failure(self):
        self.basedir = "cli/MagicFolder/create-join-failure"
        os.makedirs(self.basedir)

        o = magic_folder_cli.JoinOptions()
        o.parent = magic_folder_cli.MagicFolderCommand()
        o.parent['node-directory'] = self.basedir
        try:
            o.parseArgs("URI:invite+URI:code", "-foo")
        except usage.UsageError as e:
            self.failUnlessIn("cannot start with '-'", str(e))
        else:
            self.fail("expected UsageError")

    def test_join_twice_failure(self):
        self.basedir = "cli/MagicFolder/create-join-twice-failure"
        os.makedirs(self.basedir)
        self.set_up_grid()
        local_dir = os.path.join(self.basedir, "magic")
        abs_local_dir_u = abspath_expanduser_unicode(unicode(local_dir), long_path=False)

        d = self.do_create_magic_folder(0)
        d.addCallback(lambda ign: self.do_invite(0, u"Alice"))
        def get_invite_code_and_join((rc, stdout, stderr)):
            self.invite_code = stdout.strip()
            return self.do_join(0, unicode(local_dir), self.invite_code)
        d.addCallback(get_invite_code_and_join)
        def get_caps(ign):
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_caps)
        d.addCallback(lambda ign: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, abs_local_dir_u))
        def join_again(ignore):
            return self.do_cli("magic-folder", "join", self.invite_code, local_dir, client_num=0)
        d.addCallback(join_again)
        def get_results(result):
            code = result[0]
            self.failIfEqual(code, 0)
        d.addCallback(get_results)
        return d

    def test_join_leave_join(self):
        self.basedir = "cli/MagicFolder/create-join-leave-join"
        os.makedirs(self.basedir)
        self.set_up_grid()
        local_dir = os.path.join(self.basedir, "magic")
        abs_local_dir_u = abspath_expanduser_unicode(unicode(local_dir), long_path=False)

        self.invite_code = None
        d = self.do_create_magic_folder(0)
        d.addCallback(lambda ign: self.do_invite(0, u"Alice"))
        def get_invite_code_and_join((rc, stdout, stderr)):
            self.invite_code = stdout.strip()
            return self.do_join(0, unicode(local_dir), self.invite_code)
        d.addCallback(get_invite_code_and_join)
        def get_caps(ign):
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_caps)
        d.addCallback(lambda ign: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, abs_local_dir_u))

        def leave(ignore):
            return self.do_cli("magic-folder", "leave", client_num=0)
        d.addCallback(leave)

        def check_join_again(invite_code):
            d2 = defer.succeed(None)
            def join_again(ignore):
                return self.do_join(0, unicode(local_dir), self.invite_code)
                #return self.do_cli("magic-folder", "join", invite_code, unicode(local_dir), client_num=0)
            d2.addCallback(join_again)
            def get_results(result):
                code = result[0]
                #self.failIfEqual(code, 0)
            d2.addCallback(get_results)
            return d2

        d.addCallback(lambda ign, invite_code: check_join_again(invite_code), self.invite_code)
        def get_caps(ign):
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_caps)
        d.addCallback(lambda ign: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, abs_local_dir_u))

        return d

    def test_join_failures(self):
        self.basedir = "cli/MagicFolder/create-join-failures"
        os.makedirs(self.basedir)
        self.set_up_grid()
        local_dir = os.path.join(self.basedir, "magic")
        abs_local_dir_u = abspath_expanduser_unicode(unicode(local_dir), long_path=False)

        self.invite_code = None
        d = self.do_create_magic_folder(0)
        d.addCallback(lambda ign: self.do_invite(0, u"Alice"))
        def get_invite_code_and_join((rc, stdout, stderr)):
            self.invite_code = stdout.strip()
            return self.do_join(0, unicode(local_dir), self.invite_code)
        d.addCallback(get_invite_code_and_join)
        def get_caps(ign):
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_caps)
        d.addCallback(lambda ign: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, abs_local_dir_u))

        def leave(ignore):
            return self.do_cli("magic-folder", "leave", client_num=0)
        d.addCallback(leave)

        collective_dircap_file = os.path.join(self.get_clientdir(i=0), u"private", u"collective_dircap")
        upload_dircap = os.path.join(self.get_clientdir(i=0), u"private", u"magic_folder_dircap")
        magic_folder_db_file = os.path.join(self.get_clientdir(i=0), u"private", u"magicfolderdb.sqlite")

        def check_join_if_file(my_file):
            fileutil.write(my_file, "my file data")
            d2 = defer.succeed(None)
            def join_again(ignore):
                return self.do_cli("magic-folder", "join", self.invite_code, local_dir, client_num=0)
            d2.addCallback(join_again)
            def get_results(result):
                code = result[0]
                self.failIfEqual(code, 0)
            d2.addCallback(get_results)
            return d2

        for my_file in [collective_dircap_file, upload_dircap, magic_folder_db_file]:
            d.addCallback(lambda ign, my_file: check_join_if_file(my_file), my_file)
            d.addCallback(leave)

        return d
