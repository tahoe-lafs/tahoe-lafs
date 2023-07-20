"""
Define a protocol for listening on a transport such that Tahoe-LAFS can
communicate over it, manage configuration for it in its configuration file,
detect when it is possible to use it, etc.
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence, Mapping, Optional, Union, Awaitable
from typing_extensions import Literal

from attrs import frozen
from twisted.python.usage import Options

from .interfaces import IAddressFamily
from .util.iputil import allocate_tcp_port
from .node import _Config

@frozen
class ListenerConfig:
    """
    :ivar tub_ports: Entries to merge into ``[node]tub.port``.

    :ivar tub_locations: Entries to merge into ``[node]tub.location``.

    :ivar node_config: Entries to add into the overall Tahoe-LAFS
        configuration beneath a section named after this listener.
    """
    tub_ports: Sequence[str]
    tub_locations: Sequence[str]
    node_config: Mapping[str, Sequence[tuple[str, str]]]

class Listener(Protocol):
    """
    An object which can listen on a transport and allow Tahoe-LAFS
    communication to happen over it.
    """
    def is_available(self) -> bool:
        """
        Can this type of listener actually be used in this runtime
        environment?
        """

    def can_hide_ip(self) -> bool:
        """
        Can the transport supported by this type of listener conceal the
        node's public internet address from peers?
        """

    async def create_config(self, reactor: Any, cli_config: Options) -> Optional[ListenerConfig]:
        """
        Set up an instance of this listener according to the given
        configuration parameters.

        This may also allocate ephemeral resources if necessary.

        :return: The created configuration which can be merged into the
            overall *tahoe.cfg* configuration file.
        """

    def create(self, reactor: Any, config: _Config) -> IAddressFamily:
        """
        Instantiate this listener according to the given
        previously-generated configuration.

        :return: A handle on the listener which can be used to integrate it
            into the Tahoe-LAFS node.
        """

class TCPProvider:
    """
    Support plain TCP connections.
    """
    def is_available(self) -> Literal[True]:
        return True

    def can_hide_ip(self) -> Literal[False]:
        return False

    async def create_config(self, reactor: Any, cli_config: Options) -> ListenerConfig:
        tub_ports = []
        tub_locations = []
        if cli_config["port"]: # --port/--location are a pair
            tub_ports.append(cli_config["port"])
            tub_locations.append(cli_config["location"])
        else:
            assert "hostname" in cli_config
            hostname = cli_config["hostname"]
            new_port = allocate_tcp_port()
            tub_ports.append(f"tcp:{new_port}")
            tub_locations.append(f"tcp:{hostname}:{new_port}")

        return ListenerConfig(tub_ports, tub_locations, {})

    def create(self, reactor: Any, config: _Config) -> IAddressFamily:
        raise NotImplementedError()


@frozen
class StaticProvider:
    """
    A provider that uses all pre-computed values.
    """
    _available: bool
    _hide_ip: bool
    _config: Union[Awaitable[Optional[ListenerConfig]], Optional[ListenerConfig]]
    _address: IAddressFamily

    def is_available(self) -> bool:
        return self._available

    def can_hide_ip(self) -> bool:
        return self._hide_ip

    async def create_config(self, reactor: Any, cli_config: Options) -> Optional[ListenerConfig]:
        if self._config is None or isinstance(self._config, ListenerConfig):
            return self._config
        return await self._config

    def create(self, reactor: Any, config: _Config) -> IAddressFamily:
        return self._address
