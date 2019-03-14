import json
import shutil
import os.path
import mock
import re
import time
from datetime import datetime

from eliot import (
    log_call,
    start_action,
)
from eliot.twisted import (
    DeferredContext,
)

from twisted.trial import unittest
from twisted.internet import defer
from twisted.internet import reactor
from twisted.python import usage

from allmydata.util.assertutil import precondition
from allmydata.util import fileutil
from allmydata.scripts.common import get_aliases
from ..no_network import GridTestMixin
from ..common_util import parse_cli
from .common import CLITestMixin
from allmydata.test.common_util import NonASCIIPathMixin
from allmydata.scripts import magic_folder_cli
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.util.encodingutil import unicode_to_argv
from allmydata.frontends.magic_folder import MagicFolder
from allmydata import uri
from ...util.eliotutil import (
    log_call_deferred,
)

class MagicFolderCLITestMixin(CLITestMixin, GridTestMixin, NonASCIIPathMixin):
    def setUp(self):
        GridTestMixin.setUp(self)
        self.alice_nickname = self.unicode_or_fallback(u"Alice\u00F8", u"Alice", io_as_well=True)
        self.bob_nickname = self.unicode_or_fallback(u"Bob\u00F8", u"Bob", io_as_well=True)

    def do_create_magic_folder(self, client_num):
        with start_action(action_type=u"create-magic-folder", client_num=client_num).context():
            d = DeferredContext(
                self.do_cli(
                    "magic-folder", "--debug", "create", "magic:",
                    client_num=client_num,
                )
            )
        def _done((rc,stdout,stderr)):
            self.failUnlessEqual(rc, 0, stdout + stderr)
            self.assertIn("Alias 'magic' created", stdout)
#            self.failUnlessIn("joined new magic-folder", stdout)
#            self.failUnlessIn("Successfully created magic-folder", stdout)
            self.failUnlessEqual(stderr, "")
            aliases = get_aliases(self.get_clientdir(i=client_num))
            self.assertIn("magic", aliases)
            self.failUnless(aliases["magic"].startswith("URI:DIR2:"))
        d.addCallback(_done)
        return d.addActionFinish()

    def do_invite(self, client_num, nickname):
        nickname_arg = unicode_to_argv(nickname)
        action = start_action(
            action_type=u"invite-to-magic-folder",
            client_num=client_num,
            nickname=nickname,
        )
        with action.context():
            d = DeferredContext(
                self.do_cli(
                    "magic-folder",
                    "invite",
                    "magic:",
                    nickname_arg,
                    client_num=client_num,
                )
            )
        def _done((rc, stdout, stderr)):
            self.failUnlessEqual(rc, 0)
            return (rc, stdout, stderr)
        d.addCallback(_done)
        return d.addActionFinish()

    def do_list(self, client_num, json=False):
        args = ("magic-folder", "list",)
        if json:
            args = args + ("--json",)
        d = self.do_cli(*args, client_num=client_num)
        def _done((rc, stdout, stderr)):
            return (rc, stdout, stderr)
        d.addCallback(_done)
        return d

    def do_status(self, client_num, name=None):
        args = ("magic-folder", "status",)
        if name is not None:
            args = args + ("--name", name)
        d = self.do_cli(*args, client_num=client_num)
        def _done((rc, stdout, stderr)):
            return (rc, stdout, stderr)
        d.addCallback(_done)
        return d

    def do_join(self, client_num, local_dir, invite_code):
        action = start_action(
            action_type=u"join-magic-folder",
            client_num=client_num,
            local_dir=local_dir,
            invite_code=invite_code,
        )
        with action.context():
            precondition(isinstance(local_dir, unicode), local_dir=local_dir)
            precondition(isinstance(invite_code, str), invite_code=invite_code)
            local_dir_arg = unicode_to_argv(local_dir)
            d = DeferredContext(
                self.do_cli(
                    "magic-folder",
                    "join",
                    invite_code,
                    local_dir_arg,
                    client_num=client_num,
                )
            )
        def _done((rc, stdout, stderr)):
            self.failUnlessEqual(rc, 0)
            self.failUnlessEqual(stdout, "")
            self.failUnlessEqual(stderr, "")
            return (rc, stdout, stderr)
        d.addCallback(_done)
        return d.addActionFinish()

    def do_leave(self, client_num):
        d = self.do_cli("magic-folder", "leave", client_num=client_num)
        def _done((rc, stdout, stderr)):
            self.failUnlessEqual(rc, 0)
            return (rc, stdout, stderr)
        d.addCallback(_done)
        return d

    def check_joined_config(self, client_num, upload_dircap):
        """Tests that our collective directory has the readonly cap of
        our upload directory.
        """
        action = start_action(action_type=u"check-joined-config")
        with action.context():
            collective_readonly_cap = self.get_caps_from_files(client_num)[0]
            d = DeferredContext(
                self.do_cli(
                    "ls", "--json",
                    collective_readonly_cap,
                    client_num=client_num,
                )
            )
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
        return d.addActionFinish()

    def get_caps_from_files(self, client_num):
        from allmydata.frontends.magic_folder import load_magic_folders
        folders = load_magic_folders(self.get_clientdir(i=client_num))
        mf = folders["default"]
        return mf['collective_dircap'], mf['upload_dircap']

    @log_call
    def check_config(self, client_num, local_dir):
        client_config = fileutil.read(os.path.join(self.get_clientdir(i=client_num), "tahoe.cfg"))
        mf_yaml = fileutil.read(os.path.join(self.get_clientdir(i=client_num), "private", "magic_folders.yaml"))
        local_dir_utf8 = local_dir.encode('utf-8')
        magic_folder_config = "[magic_folder]\nenabled = True"
        self.assertIn(magic_folder_config, client_config)
        self.assertIn(local_dir_utf8, mf_yaml)

    def create_invite_join_magic_folder(self, nickname, local_dir):
        nickname_arg = unicode_to_argv(nickname)
        local_dir_arg = unicode_to_argv(local_dir)
        # the --debug means we get real exceptions on failures
        d = self.do_cli("magic-folder", "--debug", "create", "magic:", nickname_arg, local_dir_arg)
        def _done((rc, stdout, stderr)):
            self.failUnlessEqual(rc, 0, stdout + stderr)

            client = self.get_client()
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
            self.collective_dirnode = client.create_node_from_uri(self.collective_dircap)
            self.upload_dirnode     = client.create_node_from_uri(self.upload_dircap)
        d.addCallback(_done)
        d.addCallback(lambda ign: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, local_dir))
        return d

    # XXX should probably just be "tearDown"...
    @log_call_deferred(action_type=u"test:cli:magic-folder:cleanup")
    def cleanup(self, res):
        d = DeferredContext(defer.succeed(None))
        def _clean(ign):
            return self.magicfolder.disownServiceParent()

        d.addCallback(_clean)
        d.addCallback(lambda ign: res)
        return d.result

    def init_magicfolder(self, client_num, upload_dircap, collective_dircap, local_magic_dir, clock):
        dbfile = abspath_expanduser_unicode(u"magicfolder_default.sqlite", base=self.get_clientdir(i=client_num))
        magicfolder = MagicFolder(
            client=self.get_client(client_num),
            upload_dircap=upload_dircap,
            collective_dircap=collective_dircap,
            local_path_u=local_magic_dir,
            dbfile=dbfile,
            umask=0o077,
            name='default',
            clock=clock,
            uploader_delay=0.2,
            downloader_delay=0,
        )

        magicfolder.setServiceParent(self.get_client(client_num))
        magicfolder.ready()
        return magicfolder

    def setup_alice_and_bob(self, alice_clock=reactor, bob_clock=reactor):
        self.set_up_grid(num_clients=2, oneshare=True)

        self.alice_magicfolder = None
        self.bob_magicfolder = None

        alice_magic_dir = abspath_expanduser_unicode(u"Alice-magic", base=self.basedir)
        self.mkdir_nonascii(alice_magic_dir)
        bob_magic_dir = abspath_expanduser_unicode(u"Bob-magic", base=self.basedir)
        self.mkdir_nonascii(bob_magic_dir)

        # Alice creates a Magic Folder, invites herself and joins.
        d = self.do_create_magic_folder(0)
        d.addCallback(lambda ign: self.do_invite(0, self.alice_nickname))
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
        d.addCallback(lambda ign: self.do_invite(0, self.bob_nickname))
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


class ListMagicFolder(MagicFolderCLITestMixin, unittest.TestCase):

    @defer.inlineCallbacks
    def setUp(self):
        yield super(ListMagicFolder, self).setUp()
        self.basedir="mf_list"
        self.set_up_grid(oneshare=True)
        self.local_dir = os.path.join(self.basedir, "magic")
        os.mkdir(self.local_dir)
        self.abs_local_dir_u = abspath_expanduser_unicode(unicode(self.local_dir), long_path=False)

        yield self.do_create_magic_folder(0)
        (rc, stdout, stderr) = yield self.do_invite(0, self.alice_nickname)
        invite_code = stdout.strip()
        yield self.do_join(0, unicode(self.local_dir), invite_code)

    @defer.inlineCallbacks
    def tearDown(self):
        yield super(ListMagicFolder, self).tearDown()
        shutil.rmtree(self.basedir)

    @defer.inlineCallbacks
    def test_list(self):
        rc, stdout, stderr = yield self.do_list(0)
        self.failUnlessEqual(rc, 0)
        self.assertIn("default:", stdout)

    @defer.inlineCallbacks
    def test_list_none(self):
        yield self.do_leave(0)
        rc, stdout, stderr = yield self.do_list(0)
        self.failUnlessEqual(rc, 0)
        self.assertIn("No magic-folders", stdout)

    @defer.inlineCallbacks
    def test_list_json(self):
        rc, stdout, stderr = yield self.do_list(0, json=True)
        self.failUnlessEqual(rc, 0)
        res = json.loads(stdout)
        self.assertEqual(
            dict(default=dict(directory=self.abs_local_dir_u)),
            res,
        )


class StatusMagicFolder(MagicFolderCLITestMixin, unittest.TestCase):

    @defer.inlineCallbacks
    def setUp(self):
        yield super(StatusMagicFolder, self).setUp()
        self.basedir="mf_list"
        self.set_up_grid(oneshare=True)
        self.local_dir = os.path.join(self.basedir, "magic")
        os.mkdir(self.local_dir)
        self.abs_local_dir_u = abspath_expanduser_unicode(unicode(self.local_dir), long_path=False)

        yield self.do_create_magic_folder(0)
        (rc, stdout, stderr) = yield self.do_invite(0, self.alice_nickname)
        invite_code = stdout.strip()
        yield self.do_join(0, unicode(self.local_dir), invite_code)

    @defer.inlineCallbacks
    def tearDown(self):
        yield super(StatusMagicFolder, self).tearDown()
        shutil.rmtree(self.basedir)

    @defer.inlineCallbacks
    def test_status(self):
        now = datetime.now()
        then = now.replace(year=now.year - 5)
        five_year_interval = (now - then).total_seconds()

        def json_for_cap(options, cap):
            if cap.startswith('URI:DIR2:'):
                return (
                    'dirnode',
                    {
                        "children": {
                            "foo": ('filenode', {
                                "size": 1234,
                                "metadata": {
                                    "tahoe": {
                                        "linkcrtime": (time.time() - five_year_interval),
                                    },
                                    "version": 1,
                                },
                                "ro_uri": "read-only URI",
                            })
                        }
                    }
                )
            else:
                return ('dirnode', {"children": {}})
        jc = mock.patch(
            "allmydata.scripts.magic_folder_cli._get_json_for_cap",
            side_effect=json_for_cap,
        )

        def json_for_frag(options, fragment, method='GET', post_args=None):
            return {}
        jf = mock.patch(
            "allmydata.scripts.magic_folder_cli._get_json_for_fragment",
            side_effect=json_for_frag,
        )

        with jc, jf:
            rc, stdout, stderr = yield self.do_status(0)
            self.failUnlessEqual(rc, 0)
            self.assertIn("default", stdout)

        self.assertIn(
            "foo (1.23 kB): good, version=1, created 5 years ago",
            stdout,
        )

    @defer.inlineCallbacks
    def test_status_child_not_dirnode(self):
        def json_for_cap(options, cap):
            if cap.startswith('URI:DIR2'):
                return (
                    'dirnode',
                    {
                        "children": {
                            "foo": ('filenode', {
                                "size": 1234,
                                "metadata": {
                                    "tahoe": {
                                        "linkcrtime": 0.0,
                                    },
                                    "version": 1,
                                },
                                "ro_uri": "read-only URI",
                            })
                        }
                    }
                )
            elif cap == "read-only URI":
                return {
                    "error": "bad stuff",
                }
            else:
                return ('dirnode', {"children": {}})
        jc = mock.patch(
            "allmydata.scripts.magic_folder_cli._get_json_for_cap",
            side_effect=json_for_cap,
        )

        def json_for_frag(options, fragment, method='GET', post_args=None):
            return {}
        jf = mock.patch(
            "allmydata.scripts.magic_folder_cli._get_json_for_fragment",
            side_effect=json_for_frag,
        )

        with jc, jf:
            rc, stdout, stderr = yield self.do_status(0)
            self.failUnlessEqual(rc, 0)

        self.assertIn(
            "expected a dirnode",
            stdout + stderr,
        )

    @defer.inlineCallbacks
    def test_status_error_not_dircap(self):
        def json_for_cap(options, cap):
            if cap.startswith('URI:DIR2:'):
                return (
                    'filenode',
                    {}
                )
            else:
                return ('dirnode', {"children": {}})
        jc = mock.patch(
            "allmydata.scripts.magic_folder_cli._get_json_for_cap",
            side_effect=json_for_cap,
        )

        def json_for_frag(options, fragment, method='GET', post_args=None):
            return {}
        jf = mock.patch(
            "allmydata.scripts.magic_folder_cli._get_json_for_fragment",
            side_effect=json_for_frag,
        )

        with jc, jf:
            rc, stdout, stderr = yield self.do_status(0)
            self.failUnlessEqual(rc, 2)
        self.assertIn(
            "magic_folder_dircap isn't a directory capability",
            stdout + stderr,
        )

    @defer.inlineCallbacks
    def test_status_nothing(self):
        rc, stdout, stderr = yield self.do_status(0, name="blam")
        self.assertIn("No such magic-folder 'blam'", stderr)


class CreateMagicFolder(MagicFolderCLITestMixin, unittest.TestCase):
    def test_create_and_then_invite_join(self):
        self.basedir = "cli/MagicFolder/create-and-then-invite-join"
        self.set_up_grid(oneshare=True)
        local_dir = os.path.join(self.basedir, "magic")
        os.mkdir(local_dir)
        abs_local_dir_u = abspath_expanduser_unicode(unicode(local_dir), long_path=False)

        d = self.do_create_magic_folder(0)
        d.addCallback(lambda ign: self.do_invite(0, self.alice_nickname))
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
        self.set_up_grid(oneshare=True)

        d = self.do_cli("magic-folder", "create", "m a g i c:", client_num=0)
        def _done((rc, stdout, stderr)):
            self.failIfEqual(rc, 0)
            self.failUnlessIn("Alias names cannot contain spaces.", stderr)
        d.addCallback(_done)
        return d

    @defer.inlineCallbacks
    def test_create_duplicate_name(self):
        self.basedir = "cli/MagicFolder/create-dup"
        self.set_up_grid(oneshare=True)

        rc, stdout, stderr = yield self.do_cli(
            "magic-folder", "create", "magic:", "--name", "foo",
            client_num=0,
        )
        self.assertEqual(rc, 0)

        rc, stdout, stderr = yield self.do_cli(
            "magic-folder", "create", "magic:", "--name", "foo",
            client_num=0,
        )
        self.assertEqual(rc, 1)
        self.assertIn(
            "Already have a magic-folder named 'default'",
            stderr
        )

    @defer.inlineCallbacks
    def test_leave_wrong_folder(self):
        self.basedir = "cli/MagicFolder/leave_wrong_folders"
        yield self.set_up_grid(oneshare=True)
        magic_dir = os.path.join(self.basedir, 'magic')
        os.mkdir(magic_dir)

        rc, stdout, stderr = yield self.do_cli(
            "magic-folder", "create", "--name", "foo", "magic:", "my_name", magic_dir,
            client_num=0,
        )
        self.assertEqual(rc, 0)

        rc, stdout, stderr = yield self.do_cli(
            "magic-folder", "leave", "--name", "bar",
            client_num=0,
        )
        self.assertNotEqual(rc, 0)
        self.assertIn(
            "No such magic-folder 'bar'",
            stdout + stderr,
        )

    @defer.inlineCallbacks
    def test_leave_no_folder(self):
        self.basedir = "cli/MagicFolder/leave_no_folders"
        yield self.set_up_grid(oneshare=True)
        magic_dir = os.path.join(self.basedir, 'magic')
        os.mkdir(magic_dir)

        rc, stdout, stderr = yield self.do_cli(
            "magic-folder", "create", "--name", "foo", "magic:", "my_name", magic_dir,
            client_num=0,
        )
        self.assertEqual(rc, 0)

        rc, stdout, stderr = yield self.do_cli(
            "magic-folder", "leave", "--name", "foo",
            client_num=0,
        )
        self.assertEqual(rc, 0)

        rc, stdout, stderr = yield self.do_cli(
            "magic-folder", "leave", "--name", "foo",
            client_num=0,
        )
        self.assertEqual(rc, 1)
        self.assertIn(
            "No magic-folders at all",
            stderr,
        )

    @defer.inlineCallbacks
    def test_leave_no_folders_at_all(self):
        self.basedir = "cli/MagicFolder/leave_no_folders_at_all"
        yield self.set_up_grid(oneshare=True)

        rc, stdout, stderr = yield self.do_cli(
            "magic-folder", "leave",
            client_num=0,
        )
        self.assertEqual(rc, 1)
        self.assertIn(
            "No magic-folders at all",
            stderr,
        )

    def test_create_invite_join(self):
        self.basedir = "cli/MagicFolder/create-invite-join"
        self.set_up_grid(oneshare=True)
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

    def test_help_synopsis(self):
        self.basedir = "cli/MagicFolder/help_synopsis"
        os.makedirs(self.basedir)

        o = magic_folder_cli.CreateOptions()
        o.parent = magic_folder_cli.MagicFolderCommand()
        o.parent.getSynopsis()

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
        self.set_up_grid(oneshare=True)
        local_dir = os.path.join(self.basedir, "magic")
        abs_local_dir_u = abspath_expanduser_unicode(unicode(local_dir), long_path=False)

        d = self.do_create_magic_folder(0)
        d.addCallback(lambda ign: self.do_invite(0, self.alice_nickname))
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
            (rc, out, err) = result
            self.failUnlessEqual(out, "")
            self.failUnlessIn("This client already has a magic-folder", err)
            self.failIfEqual(rc, 0)
        d.addCallback(get_results)
        return d

    def test_join_leave_join(self):
        self.basedir = "cli/MagicFolder/create-join-leave-join"
        os.makedirs(self.basedir)
        self.set_up_grid(oneshare=True)
        local_dir = os.path.join(self.basedir, "magic")
        abs_local_dir_u = abspath_expanduser_unicode(unicode(local_dir), long_path=False)

        self.invite_code = None
        d = self.do_create_magic_folder(0)
        d.addCallback(lambda ign: self.do_invite(0, self.alice_nickname))
        def get_invite_code_and_join((rc, stdout, stderr)):
            self.failUnlessEqual(rc, 0)
            self.invite_code = stdout.strip()
            return self.do_join(0, unicode(local_dir), self.invite_code)
        d.addCallback(get_invite_code_and_join)
        def get_caps(ign):
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_caps)
        d.addCallback(lambda ign: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, abs_local_dir_u))
        d.addCallback(lambda ign: self.do_leave(0))

        d.addCallback(lambda ign: self.do_join(0, unicode(local_dir), self.invite_code))
        def get_caps(ign):
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_caps)
        d.addCallback(lambda ign: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, abs_local_dir_u))

        return d

    def test_join_failures(self):
        self.basedir = "cli/MagicFolder/create-join-failures"
        os.makedirs(self.basedir)
        self.set_up_grid(oneshare=True)
        local_dir = os.path.join(self.basedir, "magic")
        os.mkdir(local_dir)
        abs_local_dir_u = abspath_expanduser_unicode(unicode(local_dir), long_path=False)

        self.invite_code = None
        d = self.do_create_magic_folder(0)
        d.addCallback(lambda ign: self.do_invite(0, self.alice_nickname))
        def get_invite_code_and_join((rc, stdout, stderr)):
            self.failUnlessEqual(rc, 0)
            self.invite_code = stdout.strip()
            return self.do_join(0, unicode(local_dir), self.invite_code)
        d.addCallback(get_invite_code_and_join)
        def get_caps(ign):
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_caps)
        d.addCallback(lambda ign: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, abs_local_dir_u))

        def check_success(result):
            (rc, out, err) = result
            self.failUnlessEqual(rc, 0, out + err)
        def check_failure(result):
            (rc, out, err) = result
            self.failIfEqual(rc, 0)

        def leave(ign):
            return self.do_cli("magic-folder", "leave", client_num=0)
        d.addCallback(leave)
        d.addCallback(check_success)

        magic_folder_db_file = os.path.join(self.get_clientdir(i=0), u"private", u"magicfolder_default.sqlite")

        def check_join_if_file(my_file):
            fileutil.write(my_file, "my file data")
            d2 = self.do_cli("magic-folder", "join", self.invite_code, local_dir, client_num=0)
            d2.addCallback(check_failure)
            return d2

        for my_file in [magic_folder_db_file]:
            d.addCallback(lambda ign, my_file: check_join_if_file(my_file), my_file)
            d.addCallback(leave)
            # we didn't successfully join, so leaving should be an error
            d.addCallback(check_failure)

        return d

class CreateErrors(unittest.TestCase):
    def test_poll_interval(self):
        e = self.assertRaises(usage.UsageError, parse_cli,
                              "magic-folder", "create", "--poll-interval=frog",
                              "alias:")
        self.assertEqual(str(e), "--poll-interval must be a positive integer")

        e = self.assertRaises(usage.UsageError, parse_cli,
                              "magic-folder", "create", "--poll-interval=-4",
                              "alias:")
        self.assertEqual(str(e), "--poll-interval must be a positive integer")

    def test_alias(self):
        e = self.assertRaises(usage.UsageError, parse_cli,
                              "magic-folder", "create", "no-colon")
        self.assertEqual(str(e), "An alias must end with a ':' character.")

    def test_nickname(self):
        e = self.assertRaises(usage.UsageError, parse_cli,
                              "magic-folder", "create", "alias:", "nickname")
        self.assertEqual(str(e), "If NICKNAME is specified then LOCAL_DIR must also be specified.")

class InviteErrors(unittest.TestCase):
    def test_alias(self):
        e = self.assertRaises(usage.UsageError, parse_cli,
                              "magic-folder", "invite", "no-colon")
        self.assertEqual(str(e), "An alias must end with a ':' character.")

class JoinErrors(unittest.TestCase):
    def test_poll_interval(self):
        e = self.assertRaises(usage.UsageError, parse_cli,
                              "magic-folder", "join", "--poll-interval=frog",
                              "code", "localdir")
        self.assertEqual(str(e), "--poll-interval must be a positive integer")

        e = self.assertRaises(usage.UsageError, parse_cli,
                              "magic-folder", "join", "--poll-interval=-2",
                              "code", "localdir")
        self.assertEqual(str(e), "--poll-interval must be a positive integer")
