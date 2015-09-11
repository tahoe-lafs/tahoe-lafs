import os.path
import re

from twisted.trial import unittest
from twisted.internet import defer

from allmydata.util import fileutil
from allmydata.scripts.common import get_aliases
from allmydata.test.no_network import GridTestMixin
from .test_cli import CLITestMixin
from allmydata.scripts import magic_folder_cli
from allmydata.util.fileutil import abspath_expanduser_unicode
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
        d = self.do_cli("magic-folder", "invite", u"magic:", nickname, client_num=client_num)
        def _done((rc,stdout,stderr)):
            self.failUnless(rc == 0)
            return (rc,stdout,stderr)
        d.addCallback(_done)
        return d

    def do_join(self, client_num, local_dir, invite_code):
        magic_readonly_cap, dmd_write_cap = invite_code.split(magic_folder_cli.INVITE_SEPARATOR)
        d = self.do_cli("magic-folder", "join", invite_code, local_dir, client_num=client_num)
        def _done((rc,stdout,stderr)):
            self.failUnless(rc == 0)
            return (rc,stdout,stderr)
        d.addCallback(_done)
        return d

    def check_joined_config(self, client_num, upload_dircap):
        """Tests that our collective directory has the readonly cap of
        our upload directory.
        """
        collective_readonly_cap = fileutil.read(os.path.join(self.get_clientdir(i=client_num), "private/collective_dircap"))
        d = self.do_cli("ls", "--json", collective_readonly_cap, client_num=client_num)
        def _done((rc,stdout,stderr)):
            self.failUnless(rc == 0)
            return (rc,stdout,stderr)
        d.addCallback(_done)
        def test_joined_magic_folder((rc,stdout,stderr)):
            readonly_cap = unicode(uri.from_string(upload_dircap).get_readonly().to_string(), 'utf-8')
            s = re.search(readonly_cap, stdout)
            self.failUnless(s is not None)
            return None
        d.addCallback(test_joined_magic_folder)
        return d

    def get_caps_from_files(self, client_num):
        collective_dircap = fileutil.read(os.path.join(self.get_clientdir(i=client_num), "private/collective_dircap"))
        upload_dircap = fileutil.read(os.path.join(self.get_clientdir(i=client_num), "private/magic_folder_dircap"))
        self.failIf(collective_dircap is None or upload_dircap is None)
        return collective_dircap, upload_dircap

    def check_config(self, client_num, local_dir):
        client_config = fileutil.read(os.path.join(self.get_clientdir(i=client_num), "tahoe.cfg"))
        # XXX utf-8?
        local_dir = local_dir.encode('utf-8')
        ret = re.search("\[magic_folder\]\nenabled = True\nlocal.directory = %s" % (local_dir,), client_config)
        self.failIf(ret is None)

    def create_invite_join_magic_folder(self, nickname, local_dir):
        d = self.do_cli("magic-folder", "create", u"magic:", nickname, local_dir)
        def _done((rc,stdout,stderr)):
            self.failUnless(rc == 0)
            return (rc,stdout,stderr)
        d.addCallback(_done)
        def get_alice_caps(x):
            client = self.get_client()
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
            self.collective_dirnode = client.create_node_from_uri(self.collective_dircap)
            self.upload_dirnode     = client.create_node_from_uri(self.upload_dircap)
        d.addCallback(get_alice_caps)
        d.addCallback(lambda x: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda x: self.check_config(0, local_dir))
        return d

    def cleanup(self, res):
        print "cleanup", res
        d = defer.succeed(None)
        if self.magicfolder is not None:
            d.addCallback(lambda ign: self.magicfolder.finish())
        d.addCallback(lambda ign: res)
        return d

    def init_magicfolder(self, client_num, upload_dircap, collective_dircap, local_magic_dir):
        dbfile = abspath_expanduser_unicode(u"magicfolderdb.sqlite", base=self.get_clientdir(i=client_num))
        magicfolder = MagicFolder(self.get_client(client_num), upload_dircap, collective_dircap, local_magic_dir,
                                       dbfile, inotify=self.inotify, pending_delay=0.2)
        magicfolder.setServiceParent(self.get_client(client_num))
        magicfolder.ready()
        return magicfolder

    def create_magicfolder(self, client_num=0):
        dbfile = abspath_expanduser_unicode(u"magicfolderdb.sqlite", base=self.get_clientdir(i=client_num))
        self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)

        self.magicfolder = MagicFolder(self.get_client(client_num), self.upload_dircap, self.collective_dircap, self.local_dir,
                                       dbfile, inotify=self.inotify, pending_delay=0.2)
        magicfolder.setServiceParent(self.get_client(client_num))
        return magicfolder.ready()


    def setup_alice_and_bob(self):
        self.set_up_grid(num_clients=2)

        alice_magic_dir = abspath_expanduser_unicode(u"Alice-magic", base=self.basedir)
        self.mkdir_nonascii(alice_magic_dir)
        bob_magic_dir = abspath_expanduser_unicode(u"Bob-magic", base=self.basedir)
        self.mkdir_nonascii(bob_magic_dir)

        # Alice creates a Magic Folder,
        # invites herself then and joins.
        d = self.do_create_magic_folder(0)
        d.addCallback(lambda x: self.do_invite(0, u"Alice\u00F8"))
        def get_invitecode(result):
            self.invitecode = result[1].strip()
        d.addCallback(get_invitecode)
        d.addCallback(lambda x: self.do_join(0, alice_magic_dir, self.invitecode))
        def get_alice_caps(x):
            self.alice_collective_dircap, self.alice_upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_alice_caps)
        d.addCallback(lambda x: self.check_joined_config(0, self.alice_upload_dircap))
        d.addCallback(lambda x: self.check_config(0, alice_magic_dir))
        def get_Alice_magicfolder(result):
            self.alice_magicfolder = self.init_magicfolder(0, self.alice_upload_dircap, self.alice_collective_dircap, alice_magic_dir)
            return result
        d.addCallback(get_Alice_magicfolder)

        # Alice invites Bob. Bob joins.
        d.addCallback(lambda x: self.do_invite(0, u"Bob\u00F8"))
        def get_invitecode(result):
            self.invitecode = result[1].strip()
        d.addCallback(get_invitecode)
        d.addCallback(lambda x: self.do_join(1, bob_magic_dir, self.invitecode))
        def get_bob_caps(x):
            self.bob_collective_dircap, self.bob_upload_dircap = self.get_caps_from_files(1)
        d.addCallback(get_bob_caps)
        d.addCallback(lambda x: self.check_joined_config(1, self.bob_upload_dircap))
        d.addCallback(lambda x: self.check_config(1, bob_magic_dir))
        def get_Bob_magicfolder(result):
            self.bob_magicfolder = self.init_magicfolder(1, self.bob_upload_dircap, self.bob_collective_dircap, bob_magic_dir)
            return result
        d.addCallback(get_Bob_magicfolder)

        def prepare_result(result):
            # XXX improve this
            return (self.alice_collective_dircap, self.alice_upload_dircap, self.alice_magicfolder,
                    self.bob_collective_dircap,   self.bob_upload_dircap,   self.bob_magicfolder)
        d.addCallback(prepare_result)
        return d


class CreateMagicFolder(MagicFolderCLITestMixin, unittest.TestCase):

    def test_create_and_then_invite_join(self):
        self.basedir = "cli/MagicFolder/create-and-then-invite-join"
        self.set_up_grid()
        self.local_dir = os.path.join(self.basedir, "magic")
        d = self.do_create_magic_folder(0)
        d.addCallback(lambda x: self.do_invite(0, u"Alice"))
        def get_invite((rc,stdout,stderr)):
            self.invite_code = stdout.strip()
        d.addCallback(get_invite)
        d.addCallback(lambda x: self.do_join(0, self.local_dir, self.invite_code))
        def get_caps(x):
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_caps)
        d.addCallback(lambda x: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda x: self.check_config(0, self.local_dir))
        return d

    def test_create_error(self):
        self.basedir = "cli/MagicFolder/create-error"
        self.set_up_grid()
        self.local_dir = os.path.join(self.basedir, "magic")
        d = self.do_cli("magic-folder", "create", "m a g i c:", client_num=0)
        def _done((rc,stdout,stderr)):
            self.failIfEqual(rc, 0)
            self.failUnlessIn("Alias names cannot contain spaces.", stderr)
        d.addCallback(_done)
        return d

    def test_create_invite_join(self):
        self.basedir = "cli/MagicFolder/create-invite-join"
        self.set_up_grid()
        self.local_dir = os.path.join(self.basedir, "magic")
        d = self.do_cli("magic-folder", "create", u"magic:", u"Alice", self.local_dir)
        def _done((rc,stdout,stderr)):
            self.failUnless(rc == 0)
            return (rc,stdout,stderr)
        d.addCallback(_done)
        def get_caps(x):
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_caps)
        d.addCallback(lambda x: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda x: self.check_config(0, self.local_dir))
        return d
