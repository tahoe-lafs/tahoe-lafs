"""
Define a protocol for listening on a transport such that Tahoe-LAFS can
communicate over it, manage configuration for it in its configuration file,
detect when it is possible to use it, etc.
"""

from __future__ import annotations

from typing import Any, Protocol

from attrs import frozen

from .interfaces import IAddressFamily

@frozen
class ListenerConfig:
    """
    :ivar tub_ports: Entries to merge into ``[node]tub.port``.

    :ivar tub_locations: Entries to merge into ``[node]tub.location``.

    :ivar node_config: Entries to merge into the overall Tahoe-LAFS
        configuration.  XXX Note: Sections currently merge by overwriting
        existing items with overlapping keys.  In the future it would be nice
        to merge every item sensibly (or error).
    """
    tub_ports: list[str]
    tub_locations: list[str]
    node_config: dict[str, dict[str, str]]

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

    def create_config(self, reactor: Any, cli_config: Any) -> ListenerConfig:
        """
        Set up an instance of this listener according to the given
        configuration parameters.

        This may also allocate ephemeral resources if necessary.

        :return: The created configuration which can be merged into the
            overall *tahoe.cfg* configuration file.
        """

    def create(self, config: Any, reactor: Any) -> IAddressFamily:
        """
        Instantiate this listener according to the given
        previously-generated configuration.

        :return: A handle on the listener which can be used to integrate it
            into the Tahoe-LAFS node.
        """
