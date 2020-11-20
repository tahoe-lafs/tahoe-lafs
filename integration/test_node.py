"""
Integration tests having to do with basic node behaviors (particularly
those implemented in ``allmydata/node.py``.
"""

from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

from future.utils import PY2

if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from functools import (
    partial,
)

from eliot import (
    Message,
    start_action,
)
from eliot.twisted import (
    DeferredContext,
)

from hyperlink import (
    DecodedURL,
)

from twisted.internet.defer import (
    gatherResults,
)
from twisted.internet.task import (
    deferLater,
)

from treq import (
    get,
)

from pytest_twisted import (
    inlineCallbacks,
)

from allmydata.util.eliotutil import (
    inline_callbacks,
)


def get_node_url(node):
    with open(node.get_config().get_config_path("node.url"), "rt") as fp:
        return DecodedURL.from_text(fp.read().strip())


def poll_until_true(reactor, predicate):
    d = predicate()
    def check(result):
        if result:
            return None
        return deferLater(
            reactor,
            0.01,
            lambda: poll_until_true(reactor, predicate),
        )
    d.addCallback(check)
    return d


def get_json_welcome(reactor, node):
    with start_action(action_type=u"get_json_welcome").context():
        node_url = get_node_url(node)
        d = DeferredContext(get(node_url.add("t", "json")))
        d.addCallback(lambda response: response.json())
        return d.addActionFinish()


def connected_introducer_status(welcome):
    """
    Determine whether there is a connection to an introducer.

    :param welcome: An object like the one represented by the JSON welcome
        page.

    :return: ``True`` if the welcome indicates an active connection to an
        introducer, ``False`` otherwise.
    """
    Message.log(welcome=welcome)
    introducer_statuses = welcome["introducers"]["statuses"]
    return introducer_statuses and introducer_statuses[0].startswith("Connected ")


def check_introducer_status(reactor, node):
    """
    Check a node to see if it is connected to an introducer.
    """
    with start_action(action_type=u"check-welcome").context():
        d = DeferredContext(get_json_welcome(reactor, node))
        d.addCallback(connected_introducer_status)
        return d.addActionFinish()


def on_announcement_sent(reactor, storage_node):
    """
    Return a ``Deferred`` that fires after the given storage node could have
    sent its storage announcement to an introducer.
    """
    with start_action(
            action_type=u"on_announcement_sent",
            node_dir=storage_node.node_dir,
    ).context():
        d = DeferredContext(
            poll_until_true(
                reactor,
                partial(
                    check_introducer_status,
                    reactor,
                    storage_node,
                ),
            ),
        )
        return d.addActionFinish()


def on_all_announcements_sent(reactor, storage_nodes):
    """
    Return a ``Deferred`` that fires after all of the given storage nodes
    could have sent their storage announcements to an introducer.
    """
    with start_action(action_type=u"on_all_announcements_sent").context():
        d = DeferredContext(gatherResults((
            on_announcement_sent(reactor, storage_node)
            for storage_node
            in storage_nodes
        )))
        return d.addActionFinish()


def get_storage_server_connections(reactor, node):
    """
    Return a ``Deferred`` that fires with a list of objects describing the
    given node's storage server connections.
    """
    with start_action(action_type=u"get_storage_server_connections").context():
        d = DeferredContext(get_json_welcome(reactor, node))
        d.addCallback(lambda welcome: welcome["servers"])
        return d.addActionFinish()


@inline_callbacks
@inlineCallbacks
def test_connections(reactor, alice, storage_nodes):
    """
    A client node configured with an introducer node establishes connections
    to all reachable storage servers which have also connected to that
    introducer.

    :param TahoeProcess alice: An object representing the child process
        running the client node.

    :param [TahoeProcess] storage_nodes: A list of objects representing the
        child processes running the storage nodes.
    """
    with start_action(action_type=u"test_connections"):
        yield on_all_announcements_sent(reactor, storage_nodes)

        connections = yield get_storage_server_connections(reactor, alice)
        assert len(connections) == len(storage_nodes)
