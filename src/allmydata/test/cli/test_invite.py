import os
import mock
import json
from os.path import join

from twisted.trial import unittest
from twisted.internet import defer
from ..common_util import run_cli
from ..no_network import GridTestMixin
from .common import CLITestMixin


class _FakeWormhole(object):

    def __init__(self, outgoing_messages):
        self.messages = []
        self._outgoing = outgoing_messages

    def get_code(self):
        return defer.succeed(u"6-alarmist-tuba")

    def set_code(self, code):
        self._code = code

    def get_welcome(self):
        return defer.succeed(
            json.dumps({
                u"welcome": {},
            })
        )

    def allocate_code(self):
        return None

    def send_message(self, msg):
        self.messages.append(msg)

    def get_message(self):
        return defer.succeed(self._outgoing.pop(0))

    def close(self):
        return defer.succeed(None)


def _create_fake_wormhole(outgoing_messages):
    return _FakeWormhole(outgoing_messages)


class Join(GridTestMixin, CLITestMixin, unittest.TestCase):

    @defer.inlineCallbacks
    def setUp(self):
        self.basedir = self.mktemp()
        yield super(Join, self).setUp()
        yield self.set_up_grid(oneshare=True)

    @defer.inlineCallbacks
    def test_create_node_join(self):
        """
        successfully join after an invite
        """
        node_dir = self.mktemp()

        with mock.patch('allmydata.scripts.create_node.wormhole') as w:
            fake_wh = _create_fake_wormhole([
                json.dumps({u"abilities": {u"server-v1": {}}}),
                json.dumps({
                    u"shares-needed": 1,
                    u"shares-happy": 1,
                    u"shares-total": 1,
                    u"nickname": u"somethinghopefullyunique",
                    u"introducer": u"pb://foo",
                }),
            ])
            w.create = mock.Mock(return_value=fake_wh)

            rc, out, err = yield run_cli(
                "create-client",
                "--join", "1-abysmal-ant",
                node_dir,
            )

            self.assertEqual(0, rc)
            with open(join(node_dir, 'tahoe.cfg'), 'r') as f:
                config = f.read()
            self.assertIn("pb://foo", config)
            self.assertIn(u"somethinghopefullyunique", config)

    @defer.inlineCallbacks
    def test_create_node_illegal_option(self):
        """
        Server sends JSON with unknown/illegal key
        """
        node_dir = self.mktemp()

        with mock.patch('allmydata.scripts.create_node.wormhole') as w:
            fake_wh = _create_fake_wormhole([
                json.dumps({u"abilities": {u"server-v1": {}}}),
                json.dumps({
                    u"shares-needed": 1,
                    u"shares-happy": 1,
                    u"shares-total": 1,
                    u"nickname": u"somethinghopefullyunique",
                    u"introducer": u"pb://foo",
                    u"something-else": u"not allowed",
                }),
            ])
            w.create = mock.Mock(return_value=fake_wh)

            rc, out, err = yield run_cli(
                "create-client",
                "--join", "1-abysmal-ant",
                node_dir,
            )

            # should still succeed -- just ignores the not-whitelisted
            # "something-else" option
            self.assertEqual(0, rc)


class Invite(GridTestMixin, CLITestMixin, unittest.TestCase):

    @defer.inlineCallbacks
    def setUp(self):
        self.basedir = self.mktemp()
        yield super(Invite, self).setUp()
        yield self.set_up_grid(oneshare=True)
        intro_dir = os.path.join(self.basedir, "introducer")
        yield run_cli(
            "create-introducer",
            "--listen", "none",
            intro_dir,
        )

    @defer.inlineCallbacks
    def test_invite_success(self):
        """
        successfully send an invite
        """
        intro_dir = os.path.join(self.basedir, "introducer")
        # we've never run the introducer, so it hasn't created
        # introducer.furl yet
        priv_dir = join(intro_dir, "private")
        with open(join(priv_dir, "introducer.furl"), "w") as f:
            f.write("pb://fooblam\n")

        with mock.patch('allmydata.scripts.tahoe_invite.wormhole') as w:
            fake_wh = _create_fake_wormhole([
                json.dumps({u"abilities": {u"client-v1": {}}}),
            ])
            w.create = mock.Mock(return_value=fake_wh)

            rc, out, err = yield run_cli(
                "-d", intro_dir,
                "invite",
                "--shares-needed", "1",
                "--shares-happy", "1",
                "--shares-total", "1",
                "foo",
            )
            self.assertEqual(2, len(fake_wh.messages))
            self.assertEqual(
                json.loads(fake_wh.messages[0]),
                {
                    "abilities":
                    {
                        "server-v1": {}
                    },
                },
            )
            self.assertEqual(
                json.loads(fake_wh.messages[1]),
                {
                    "shares-needed": "1",
                    "shares-total": "1",
                    "nickname": "foo",
                    "introducer": "pb://fooblam",
                    "shares-happy": "1",
                },
            )

    @defer.inlineCallbacks
    def test_invite_no_furl(self):
        """
        Invites must include the Introducer FURL
        """
        intro_dir = os.path.join(self.basedir, "introducer")

        with mock.patch('allmydata.scripts.tahoe_invite.wormhole') as w:
            fake_wh = _create_fake_wormhole([
                json.dumps({u"abilities": {u"client-v1": {}}}),
            ])
            w.create = mock.Mock(return_value=fake_wh)

            rc, out, err = yield run_cli(
                "-d", intro_dir,
                "invite",
                "--shares-needed", "1",
                "--shares-happy", "1",
                "--shares-total", "1",
                "foo",
            )
            self.assertNotEqual(rc, 0)
            self.assertIn(u"Can't find introducer FURL", out + err)

    @defer.inlineCallbacks
    def test_invite_wrong_client_abilities(self):
        """
        Send unknown client version
        """
        intro_dir = os.path.join(self.basedir, "introducer")
        # we've never run the introducer, so it hasn't created
        # introducer.furl yet
        priv_dir = join(intro_dir, "private")
        with open(join(priv_dir, "introducer.furl"), "w") as f:
            f.write("pb://fooblam\n")

        with mock.patch('allmydata.scripts.tahoe_invite.wormhole') as w:
            fake_wh = _create_fake_wormhole([
                json.dumps({u"abilities": {u"client-v9000": {}}}),
            ])
            w.create = mock.Mock(return_value=fake_wh)

            rc, out, err = yield run_cli(
                "-d", intro_dir,
                "invite",
                "--shares-needed", "1",
                "--shares-happy", "1",
                "--shares-total", "1",
                "foo",
            )
            self.assertNotEqual(rc, 0)
            self.assertIn(u"No 'client-v1' in abilities", out + err)

    @defer.inlineCallbacks
    def test_invite_no_client_abilities(self):
        """
        Client doesn't send any client abilities at all
        """
        intro_dir = os.path.join(self.basedir, "introducer")
        # we've never run the introducer, so it hasn't created
        # introducer.furl yet
        priv_dir = join(intro_dir, "private")
        with open(join(priv_dir, "introducer.furl"), "w") as f:
            f.write("pb://fooblam\n")

        with mock.patch('allmydata.scripts.tahoe_invite.wormhole') as w:
            fake_wh = _create_fake_wormhole([
                json.dumps({}),
            ])
            w.create = mock.Mock(return_value=fake_wh)

            rc, out, err = yield run_cli(
                "-d", intro_dir,
                "invite",
                "--shares-needed", "1",
                "--shares-happy", "1",
                "--shares-total", "1",
                "foo",
            )
            self.assertNotEqual(rc, 0)
            self.assertIn(u"No 'abilities' from client", out + err)

    @defer.inlineCallbacks
    def test_invite_wrong_server_abilities(self):
        """
        Server sends unknown version
        """
        intro_dir = os.path.join(self.basedir, "introducer")
        # we've never run the introducer, so it hasn't created
        # introducer.furl yet
        priv_dir = join(intro_dir, "private")
        with open(join(priv_dir, "introducer.furl"), "w") as f:
            f.write("pb://fooblam\n")

        with mock.patch('allmydata.scripts.create_node.wormhole') as w:
            fake_wh = _create_fake_wormhole([
                json.dumps({u"abilities": {u"server-v9000": {}}}),
                json.dumps({
                    "shares-needed": "1",
                    "shares-total": "1",
                    "shares-happy": "1",
                    "nickname": "foo",
                    "introducer": "pb://fooblam",
                }),
            ])
            w.create = mock.Mock(return_value=fake_wh)

            rc, out, err = yield run_cli(
                "create-client",
                "--join", "1-alarmist-tuba",
                "foo",
            )
            self.assertNotEqual(rc, 0)
            self.assertIn("Expected 'server-v1' in server abilities", out + err)

    @defer.inlineCallbacks
    def test_invite_no_server_abilities(self):
        """
        Server sends unknown version
        """
        intro_dir = os.path.join(self.basedir, "introducer")
        # we've never run the introducer, so it hasn't created
        # introducer.furl yet
        priv_dir = join(intro_dir, "private")
        with open(join(priv_dir, "introducer.furl"), "w") as f:
            f.write("pb://fooblam\n")

        with mock.patch('allmydata.scripts.create_node.wormhole') as w:
            fake_wh = _create_fake_wormhole([
                json.dumps({}),
                json.dumps({
                    "shares-needed": "1",
                    "shares-total": "1",
                    "shares-happy": "1",
                    "nickname": "bar",
                    "introducer": "pb://fooblam",
                }),
            ])
            w.create = mock.Mock(return_value=fake_wh)

            rc, out, err = yield run_cli(
                "create-client",
                "--join", "1-alarmist-tuba",
                "bar",
            )
            self.assertNotEqual(rc, 0)
            self.assertIn("Expected 'abilities' in server introduction", out + err)

    @defer.inlineCallbacks
    def test_invite_no_nick(self):
        """
        Should still work if server sends no nickname
        """
        intro_dir = os.path.join(self.basedir, "introducer")

        with mock.patch('allmydata.scripts.tahoe_invite.wormhole'):
            rc, out, err = yield run_cli(
                "-d", intro_dir,
                "invite",
                "--shares-needed", "1",
                "--shares-happy", "1",
                "--shares-total", "1",
            )
            self.assertTrue(rc)
            self.assertIn(u"Provide a single argument", out + err)
