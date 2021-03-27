"""
Ported to Pythn 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os
import mock
import json
from os.path import join

try:
    from typing import Optional, Sequence
except ImportError:
    pass

from twisted.trial import unittest
from twisted.internet import defer
from ..common_util import run_cli
from ..no_network import GridTestMixin
from .common import CLITestMixin
from ...client import (
    read_config,
)

class _FakeWormhole(object):

    def __init__(self, outgoing_messages):
        self.messages = []
        for o in outgoing_messages:
            assert isinstance(o, bytes)
        self._outgoing = outgoing_messages

    def get_code(self):
        return defer.succeed(u"6-alarmist-tuba")

    def set_code(self, code):
        self._code = code

    def get_welcome(self):
        return defer.succeed(
            {
                u"welcome": {},
            }
        )

    def allocate_code(self):
        return None

    def send_message(self, msg):
        assert isinstance(msg, bytes)
        self.messages.append(msg)

    def get_message(self):
        return defer.succeed(self._outgoing.pop(0))

    def close(self):
        return defer.succeed(None)


def _create_fake_wormhole(outgoing_messages):
    outgoing_messages = [
        m.encode("utf-8") if isinstance(m, str) else m
        for m in outgoing_messages
    ]
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

            config = read_config(node_dir, u"")
            self.assertIn(
                "pb://foo",
                set(
                    furl
                    for (furl, cache)
                    in config.get_introducer_configuration().values()
                ),
            )

            with open(join(node_dir, 'tahoe.cfg'), 'r') as f:
                config = f.read()
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

    def _invite_success(self, extra_args=(), tahoe_config=None):
        # type: (Sequence[bytes], Optional[bytes]) -> defer.Deferred
        """
        Exercise an expected-success case of ``tahoe invite``.

        :param extra_args: Positional arguments to pass to ``tahoe invite``
            before the nickname.

        :param tahoe_config: If given, bytes to write to the node's
            ``tahoe.cfg`` before running ``tahoe invite.
        """
        intro_dir = os.path.join(self.basedir, "introducer")
        # we've never run the introducer, so it hasn't created
        # introducer.furl yet
        priv_dir = join(intro_dir, "private")
        with open(join(priv_dir, "introducer.furl"), "w") as fobj_intro:
            fobj_intro.write("pb://fooblam\n")
        if tahoe_config is not None:
            assert isinstance(tahoe_config, bytes)
            with open(join(intro_dir, "tahoe.cfg"), "wb") as fobj_cfg:
                fobj_cfg.write(tahoe_config)

        with mock.patch('allmydata.scripts.tahoe_invite.wormhole') as w:
            fake_wh = _create_fake_wormhole([
                json.dumps({u"abilities": {u"client-v1": {}}}),
            ])
            w.create = mock.Mock(return_value=fake_wh)

            extra_args = tuple(extra_args)

            d = run_cli(
                "-d", intro_dir,
                "invite",
                *(extra_args + ("foo",))
            )

            def done(result):
                rc, out, err = result
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
                invite = json.loads(fake_wh.messages[1])
                self.assertEqual(
                    invite["nickname"], "foo",
                )
                self.assertEqual(
                    invite["introducer"], "pb://fooblam",
                )
                return invite
            d.addCallback(done)
            return d

    @defer.inlineCallbacks
    def test_invite_success(self):
        """
        successfully send an invite
        """
        invite = yield self._invite_success((
            "--shares-needed", "1",
            "--shares-happy", "2",
            "--shares-total", "3",
        ))
        self.assertEqual(
            invite["shares-needed"], "1",
        )
        self.assertEqual(
            invite["shares-happy"], "2",
        )
        self.assertEqual(
            invite["shares-total"], "3",
        )

    @defer.inlineCallbacks
    def test_invite_success_read_share_config(self):
        """
        If ``--shares-{needed,happy,total}`` are not given on the command line
        then the invitation is generated using the configured values.
        """
        invite = yield self._invite_success(tahoe_config=b"""
[client]
shares.needed = 2
shares.happy = 4
shares.total = 6
""")
        self.assertEqual(
            invite["shares-needed"], "2",
        )
        self.assertEqual(
            invite["shares-happy"], "4",
        )
        self.assertEqual(
            invite["shares-total"], "6",
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
