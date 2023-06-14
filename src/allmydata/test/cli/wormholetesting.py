"""
An in-memory implementation of some of the magic-wormhole interfaces for
use by automated tests.

For example::

    async def peerA(mw):
        wormhole = mw.create("myapp", "wss://myserver", reactor)
        code = await wormhole.get_code()
        print(f"I have a code: {code}")
        message = await wormhole.when_received()
        print(f"I have a message: {message}")

    async def local_peerB(helper, mw):
        peerA_wormhole = await helper.wait_for_wormhole("myapp", "wss://myserver")
        code = await peerA_wormhole.when_code()

        peerB_wormhole = mw.create("myapp", "wss://myserver")
        peerB_wormhole.set_code(code)

        peerB_wormhole.send_message("Hello, peer A")

    # Run peerA against local_peerB with pure in-memory message passing.
    server, helper = memory_server()
    run(gather(peerA(server), local_peerB(helper, server)))

    # Run peerA against a peerB somewhere out in the world, using a real
    # wormhole relay server somewhere.
    import wormhole
    run(peerA(wormhole))
"""

from __future__ import annotations

__all__ = ['MemoryWormholeServer', 'TestingHelper', 'memory_server', 'IWormhole']

from typing import Iterator, Optional, List, Tuple, Any, TextIO
from inspect import getfullargspec
from itertools import count
from sys import stderr

from attrs import frozen, define, field, Factory
from twisted.internet.defer import Deferred, DeferredQueue, succeed
from wormhole._interfaces import IWormhole
from wormhole.wormhole import create
from zope.interface import implementer

WormholeCode = str
WormholeMessage = bytes
AppId = str
RelayURL = str
ApplicationKey = Tuple[RelayURL, AppId]

@define
class MemoryWormholeServer(object):
    """
    A factory for in-memory wormholes.

    :ivar _apps: Wormhole state arranged by the application id and relay URL
        it belongs to.

    :ivar _waiters: Observers waiting for a wormhole to be created for a
        specific application id and relay URL combination.
    """
    _apps: dict[ApplicationKey, _WormholeApp] = field(default=Factory(dict))
    _waiters: dict[ApplicationKey, Deferred[IWormhole]] = field(default=Factory(dict))

    def create(
        self,
        appid: str,
        relay_url: str,
        reactor: Any,
        # Unfortunately we need a mutable default to match the real API
        versions: Any={},  # noqa: B006
        delegate: Optional[Any]=None,
        journal: Optional[Any]=None,
        tor: Optional[Any]=None,
        timing: Optional[Any]=None,
        stderr: TextIO=stderr,
        _eventual_queue: Optional[Any]=None,
        _enable_dilate: bool=False,
    ) -> _MemoryWormhole:
        """
        Create a wormhole.  It will be able to connect to other wormholes created
        by this instance (and constrained by the normal appid/relay_url
        rules).
        """
        if tor is not None:
            raise ValueError("Cannot deal with Tor right now.")
        if _enable_dilate:
            raise ValueError("Cannot deal with dilation right now.")

        key = (relay_url, appid)
        wormhole = _MemoryWormhole(self._view(key))
        if key in self._waiters:
            self._waiters.pop(key).callback(wormhole)
        return wormhole

    def _view(self, key: ApplicationKey) -> _WormholeServerView:
        """
        Created a view onto this server's state that is limited by a certain
        appid/relay_url pair.
        """
        return _WormholeServerView(self, key)


@frozen
class TestingHelper(object):
    """
    Provide extra functionality for interacting with an in-memory wormhole
    implementation.

    This is intentionally a separate API so that it is not confused with
    proper public interface of the real wormhole implementation.
    """
    _server: MemoryWormholeServer

    async def wait_for_wormhole(self, appid: AppId, relay_url: RelayURL) -> IWormhole:
        """
        Wait for a wormhole to appear at a specific location.

        :param appid: The appid that the resulting wormhole will have.

        :param relay_url: The URL of the relay at which the resulting wormhole
            will presume to be created.

        :return: The first wormhole to be created which matches the given
            parameters.
        """
        key = (relay_url, appid)
        if key in self._server._waiters:
            raise ValueError(f"There is already a waiter for {key}")
        d : Deferred[IWormhole] = Deferred()
        self._server._waiters[key] = d
        wormhole = await d
        return wormhole


def _verify() -> None:
    """
    Roughly confirm that the in-memory wormhole creation function matches the
    interface of the real implementation.
    """
    # Poor man's interface verification.

    a = getfullargspec(create)
    b = getfullargspec(MemoryWormholeServer.create)
    # I know it has a `self` argument at the beginning.  That's okay.
    b = b._replace(args=b.args[1:])

    # Just compare the same information to check function signature
    assert a.varkw == b.varkw
    assert a.args == b.args
    assert a.varargs == b.varargs
    assert a.kwonlydefaults == b.kwonlydefaults
    assert a.defaults == b.defaults


_verify()


@define
class _WormholeApp(object):
    """
    Represent a collection of wormholes that belong to the same
    appid/relay_url scope.
    """
    wormholes: dict[WormholeCode, IWormhole] = field(default=Factory(dict))
    _waiting: dict[WormholeCode, List[Deferred[_MemoryWormhole]]] = field(default=Factory(dict))
    _counter: Iterator[int] = field(default=Factory(count))

    def allocate_code(self, wormhole: IWormhole, code: Optional[WormholeCode]) -> WormholeCode:
        """
        Allocate a new code for the given wormhole.

        This also associates the given wormhole with the code for future
        lookup.

        Code generation logic is trivial and certainly not good enough for any
        real use.  It is sufficient for automated testing, though.
        """
        if code is None:
            code = "{}-persnickety-tardigrade".format(next(self._counter))
        self.wormholes.setdefault(code, []).append(wormhole)
        try:
            waiters = self._waiting.pop(code)
        except KeyError:
            pass
        else:
            for w in waiters:
                w.callback(wormhole)

        return code

    def wait_for_wormhole(self, code: WormholeCode) -> Deferred[_MemoryWormhole]:
        """
        Return a ``Deferred`` which fires with the next wormhole to be associated
        with the given code.  This is used to let the first end of a wormhole
        rendezvous with the second end.
        """
        d : Deferred[_MemoryWormhole] = Deferred()
        self._waiting.setdefault(code, []).append(d)
        return d


@frozen
class _WormholeServerView(object):
    """
    Present an interface onto the server to be consumed by individual
    wormholes.
    """
    _server: MemoryWormholeServer
    _key: ApplicationKey

    def allocate_code(self, wormhole: _MemoryWormhole, code: Optional[WormholeCode]) -> WormholeCode:
        """
        Allocate a new code for the given wormhole in the scope associated with
        this view.
        """
        app = self._server._apps.setdefault(self._key, _WormholeApp())
        return app.allocate_code(wormhole, code)

    def wormhole_by_code(self, code: WormholeCode, exclude: object) -> Deferred[IWormhole]:
        """
        Retrieve all wormholes previously associated with a code.
        """
        app = self._server._apps[self._key]
        wormholes = app.wormholes[code]
        try:
            [wormhole] = list(wormhole for wormhole in wormholes if wormhole != exclude)
        except ValueError:
            return app.wait_for_wormhole(code)
        return succeed(wormhole)


@implementer(IWormhole)
@define
class _MemoryWormhole(object):
    """
    Represent one side of a wormhole as conceived by ``MemoryWormholeServer``.
    """

    _view: _WormholeServerView
    _code: Optional[WormholeCode] = None
    _payload: DeferredQueue[WormholeMessage] = field(default=Factory(DeferredQueue))
    _waiting_for_code: list[Deferred[WormholeCode]] = field(default=Factory(list))

    def allocate_code(self) -> None:
        if self._code is not None:
            raise ValueError(
                "allocate_code used with a wormhole which already has a code"
            )
        self._code = self._view.allocate_code(self, None)
        waiters = self._waiting_for_code
        self._waiting_for_code = []
        for d in waiters:
            d.callback(self._code)

    def set_code(self, code: WormholeCode) -> None:
        if self._code is None:
            self._code = code
            self._view.allocate_code(self, code)
        else:
            raise ValueError("set_code used with a wormhole which already has a code")

    def when_code(self) -> Deferred[WormholeCode]:
        if self._code is None:
            d : Deferred[WormholeCode] = Deferred()
            self._waiting_for_code.append(d)
            return d
        return succeed(self._code)

    def get_welcome(self) -> Deferred[str]:
        return succeed("welcome")

    def send_message(self, payload: WormholeMessage) -> None:
        self._payload.put(payload)

    def when_received(self) -> Deferred[WormholeMessage]:
        if self._code is None:
            raise ValueError(
                "This implementation requires set_code or allocate_code "
                "before when_received."
            )
        d = self._view.wormhole_by_code(self._code, exclude=self)

        def got_wormhole(wormhole: _MemoryWormhole) -> Deferred[WormholeMessage]:
            msg: Deferred[WormholeMessage] = wormhole._payload.get()
            return msg

        d.addCallback(got_wormhole)
        return d

    get_message = when_received

    def close(self) -> None:
        pass

    # 0.9.2 compatibility
    def get_code(self) -> Deferred[WormholeCode]:
        if self._code is None:
            self.allocate_code()
        return self.when_code()

    get = when_received


def memory_server() -> tuple[MemoryWormholeServer, TestingHelper]:
    """
    Create a paired in-memory wormhole server and testing helper.
    """
    server = MemoryWormholeServer()
    return server, TestingHelper(server)
