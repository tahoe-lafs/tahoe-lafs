"""
Tests for ``tahoe invite``.
"""

from __future__ import annotations

import json
import os
from functools import partial
from os.path import join
from typing import Callable, Optional, Sequence, TypeVar, Union, Coroutine, Any, Tuple, cast, Generator

from twisted.internet import defer
from twisted.trial import unittest

from ...client import read_config
from ...scripts import runner
from ...util.jsonbytes import dumps_bytes
from ..common_util import run_cli
from ..no_network import GridTestMixin
from .common import CLITestMixin
from .wormholetesting import MemoryWormholeServer, TestingHelper, memory_server, IWormhole


# Logically:
#   JSONable = dict[str, Union[JSONable, None, int, float, str, list[JSONable]]]
#
# But practically:
JSONable = Union[dict, None, int, float, str, list]


async def open_wormhole() -> tuple[Callable, IWormhole, str]:
    """
    Create a new in-memory wormhole server, open one end of a wormhole, and
    return it and related info.

    :return: A three-tuple allowing use of the wormhole.  The first element is
        a callable like ``run_cli`` but which will run commands so that they
        use the in-memory wormhole server instead of a real one.  The second
        element is the open wormhole.  The third element is the wormhole's
        code.
    """
    server = MemoryWormholeServer()
    options = runner.Options()
    options.wormhole = server
    reactor = object()

    wormhole = server.create(
        "tahoe-lafs.org/invite",
        "ws://wormhole.tahoe-lafs.org:4000/v1",
        reactor,
    )
    code = await wormhole.get_code()

    return (partial(run_cli, options=options), wormhole, code)


def make_simple_peer(
        reactor,
        server: MemoryWormholeServer,
        helper: TestingHelper,
        messages: Sequence[JSONable],
) -> Callable[[], Coroutine[defer.Deferred[IWormhole], Any, IWormhole]]:
    """
    Make a wormhole peer that just sends the given messages.

    The returned function returns an awaitable that fires with the peer's end
    of the wormhole.
    """
    async def peer() -> IWormhole:
        # Run the client side of the invitation by manually pumping a
        # message through the wormhole.

        # First, wait for the server to create the wormhole at all.
        wormhole = await helper.wait_for_wormhole(
            "tahoe-lafs.org/invite",
            "ws://wormhole.tahoe-lafs.org:4000/v1",
        )
        # Then read out its code and open the other side of the wormhole.
        code = await wormhole.when_code()
        other_end = server.create(
            "tahoe-lafs.org/invite",
            "ws://wormhole.tahoe-lafs.org:4000/v1",
            reactor,
        )
        other_end.set_code(code)
        send_messages(other_end, messages)
        return other_end

    return peer


def send_messages(wormhole: IWormhole, messages: Sequence[JSONable]) -> None:
    """
    Send a list of message through a wormhole.
    """
    for msg in messages:
        wormhole.send_message(dumps_bytes(msg))


A = TypeVar("A")
B = TypeVar("B")

def concurrently(
    client: Callable[[], Union[
        Coroutine[defer.Deferred[A], Any, A],
        Generator[defer.Deferred[A], Any, A],
    ]],
    server: Callable[[], Union[
        Coroutine[defer.Deferred[B], Any, B],
        Generator[defer.Deferred[B], Any, B],
    ]],
) -> defer.Deferred[Tuple[A, B]]:
    """
    Run two asynchronous functions concurrently and asynchronously return a
    tuple of both their results.
    """
    result = defer.gatherResults([
        defer.Deferred.fromCoroutine(client()),
        defer.Deferred.fromCoroutine(server()),
    ]).addCallback(tuple)  # type: ignore
    return cast(defer.Deferred[Tuple[A, B]], result)

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
        run_cli, wormhole, code = yield defer.Deferred.fromCoroutine(open_wormhole())
        send_messages(wormhole, [
            {u"abilities": {u"server-v1": {}}},
            {
                u"shares-needed": 1,
                u"shares-happy": 1,
                u"shares-total": 1,
                u"nickname": u"somethinghopefullyunique",
                u"introducer": u"pb://foo",
            },
        ])

        rc, out, err = yield run_cli(
            "create-client",
            "--join", code,
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
        run_cli, wormhole, code = yield defer.Deferred.fromCoroutine(open_wormhole())
        send_messages(wormhole, [
            {u"abilities": {u"server-v1": {}}},
            {
                u"shares-needed": 1,
                u"shares-happy": 1,
                u"shares-total": 1,
                u"nickname": u"somethinghopefullyunique",
                u"introducer": u"pb://foo",
                u"something-else": u"not allowed",
            },
        ])

        rc, out, err = yield run_cli(
            "create-client",
            "--join", code,
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

    async def _invite_success(self, extra_args: Sequence[bytes] = (), tahoe_config: Optional[bytes] = None) -> str:
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

        wormhole_server, helper = memory_server()
        options = runner.Options()
        options.wormhole = wormhole_server
        reactor = object()

        async def server():
            # Run the server side of the invitation process using the CLI.
            rc, out, err = await run_cli(
                "-d", intro_dir,
                "invite",
                *tuple(extra_args) + ("foo",),
                options=options,
            )

        # Send a proper client abilities message.
        client = make_simple_peer(reactor, wormhole_server, helper, [{u"abilities": {u"client-v1": {}}}])
        other_end, _ = await concurrently(client, server)

        # Check the server's messages.  First, it should announce its
        # abilities correctly.
        server_abilities = json.loads(await other_end.when_received())
        self.assertEqual(
            server_abilities,
            {
                "abilities":
                {
                    "server-v1": {}
                },
            },
        )

        # Second, it should have an invitation with a nickname and introducer
        # furl.
        invite = json.loads(await other_end.when_received())
        self.assertEqual(
            invite["nickname"], "foo",
        )
        self.assertEqual(
            invite["introducer"], "pb://fooblam",
        )
        return invite

    @defer.inlineCallbacks
    def test_invite_success(self):
        """
        successfully send an invite
        """
        invite = yield defer.Deferred.fromCoroutine(self._invite_success((
            "--shares-needed", "1",
            "--shares-happy", "2",
            "--shares-total", "3",
        )))
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
        invite = yield defer.Deferred.fromCoroutine(self._invite_success(tahoe_config=b"""
[client]
shares.needed = 2
shares.happy = 4
shares.total = 6
"""))
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

        options = runner.Options()
        options.wormhole = None

        rc, out, err = yield run_cli(
            "-d", intro_dir,
            "invite",
            "--shares-needed", "1",
            "--shares-happy", "1",
            "--shares-total", "1",
            "foo",
            options=options,
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

        wormhole_server, helper = memory_server()
        options = runner.Options()
        options.wormhole = wormhole_server
        reactor = object()

        async def server():
            rc, out, err = await run_cli(
                "-d", intro_dir,
                "invite",
                "--shares-needed", "1",
                "--shares-happy", "1",
                "--shares-total", "1",
                "foo",
                options=options,
            )
            self.assertNotEqual(rc, 0)
            self.assertIn(u"No 'client-v1' in abilities", out + err)

        # Send some surprising client abilities.
        client = make_simple_peer(reactor, wormhole_server, helper, [{u"abilities": {u"client-v9000": {}}}])
        yield concurrently(client, server)

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

        wormhole_server, helper = memory_server()
        options = runner.Options()
        options.wormhole = wormhole_server
        reactor = object()

        async def server():
            # Run the server side of the invitation process using the CLI.
            rc, out, err = await run_cli(
                "-d", intro_dir,
                "invite",
                "--shares-needed", "1",
                "--shares-happy", "1",
                "--shares-total", "1",
                "foo",
                options=options,
            )
            self.assertNotEqual(rc, 0)
            self.assertIn(u"No 'abilities' from client", out + err)

        # Send a no-abilities message through to the server.
        client = make_simple_peer(reactor, wormhole_server, helper, [{}])
        yield concurrently(client, server)


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

        run_cli, wormhole, code = yield defer.Deferred.fromCoroutine(open_wormhole())
        send_messages(wormhole, [
            {u"abilities": {u"server-v9000": {}}},
            {
                "shares-needed": "1",
                "shares-total": "1",
                "shares-happy": "1",
                "nickname": "foo",
                "introducer": "pb://fooblam",
            },
        ])

        rc, out, err = yield run_cli(
            "create-client",
            "--join", code,
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

        run_cli, wormhole, code = yield defer.Deferred.fromCoroutine(open_wormhole())
        send_messages(wormhole, [
            {},
            {
                "shares-needed": "1",
                "shares-total": "1",
                "shares-happy": "1",
                "nickname": "bar",
                "introducer": "pb://fooblam",
            },
        ])

        rc, out, err = yield run_cli(
            "create-client",
            "--join", code,
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

        options = runner.Options()
        options.wormhole = None

        rc, out, err = yield run_cli(
            "-d", intro_dir,
            "invite",
            "--shares-needed", "1",
            "--shares-happy", "1",
            "--shares-total", "1",
            options=options,
        )
        self.assertTrue(rc)
        self.assertIn(u"Provide a single argument", out + err)
