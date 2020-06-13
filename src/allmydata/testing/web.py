# -*- coding: utf-8 -*-
# Tahoe-LAFS -- secure, distributed storage grid
#
# Copyright © 2020 The Tahoe-LAFS Software Foundation
#
# This file is part of Tahoe-LAFS.
#
# See the docs/about.rst file for licensing information.

"""
Test-helpers for clients that use the WebUI.
"""

import hashlib

import attr

from hyperlink import DecodedURL

from twisted.web.resource import (
    Resource,
)
from twisted.web.iweb import (
    IBodyProducer,
)
from twisted.internet.defer import (
    succeed,
)

from treq.client import (
    HTTPClient,
    FileBodyProducer,
)
from treq.testing import (
    RequestTraversalAgent,
)
from zope.interface import implementer

import allmydata.uri
from allmydata.util import (
    base32,
)

__all__ = (
    "create_fake_tahoe_root",
    "create_tahoe_treq_client",
)


class _FakeTahoeRoot(Resource, object):
    """
    An in-memory 'fake' of a Tahoe WebUI root. Currently it only
    implements (some of) the `/uri` resource.
    """

    def __init__(self, uri=None):
        """
        :param uri: a Resource to handle the `/uri` tree.
        """
        Resource.__init__(self)  # this is an old-style class :(
        self._uri = uri
        self.putChild(b"uri", self._uri)

    def add_data(self, kind, data):
        return self._uri.add_data(kind, data)


KNOWN_CAPABILITIES = [
    getattr(allmydata.uri, t).BASE_STRING
    for t in dir(allmydata.uri)
    if hasattr(getattr(allmydata.uri, t), 'BASE_STRING')
]


def capability_generator(kind):
    """
    Deterministically generates a streap of valid capabilities of the
    given kind. The N, K and size values aren't related to anything
    real.

    :param str kind: the kind of capability, like `URI:CHK`

    :returns: a generator that yields new capablities of a particular
        kind.
    """
    if kind not in KNOWN_CAPABILITIES:
        raise ValueError(
            "Unknown capability kind '{} (valid are {})'".format(
                kind,
                ", ".join(KNOWN_CAPABILITIES),
            )
        )
    # what we do here is to start with empty hashers for the key and
    # ueb_hash and repeatedly feed() them a zero byte on each
    # iteration .. so the same sequence of capabilities will always be
    # produced. We could add a seed= argument if we wanted to produce
    # different sequences.
    number = 0
    key_hasher = hashlib.new("sha256")
    ueb_hasher = hashlib.new("sha256")  # ueb means "URI Extension Block"

    # capabilities are "prefix:<128-bits-base32>:<256-bits-base32>:N:K:size"
    while True:
        number += 1
        key_hasher.update("\x00")
        ueb_hasher.update("\x00")

        key = base32.b2a(key_hasher.digest()[:16])  # key is 16 bytes
        ueb_hash = base32.b2a(ueb_hasher.digest())  # ueb hash is 32 bytes

        cap = u"{kind}{key}:{ueb_hash}:{n}:{k}:{size}".format(
            kind=kind,
            key=key,
            ueb_hash=ueb_hash,
            n=1,
            k=1,
            size=number * 1000,
        )
        yield cap.encode("ascii")


class _FakeTahoeUriHandler(Resource, object):
    """
    An in-memory fake of (some of) the `/uri` endpoint of a Tahoe
    WebUI
    """

    isLeaf = True
    _data = None
    _capability_generators = None

    def _generate_capability(self, kind):
        """
        :param str kind: any valid capability-string type

        :returns: the next capability-string for the given kind
        """
        if self._capability_generators is None:
            self._capability_generators = dict()

        if kind not in self._capability_generators:
            self._capability_generators[kind] = capability_generator(kind)
        capability = next(self._capability_generators[kind])
        return capability

    def add_data(self, kind, data):
        """
        adds some data to our grid

        :returns: a capability-string
        """
        assert isinstance(data, bytes)

        cap = self._generate_capability(kind)
        if self._data is None:
            self._data = dict()
        assert cap not in self._data, "already have '{}'".format(cap)
        self._data[cap] = data
        return cap

    def render_PUT(self, request):
        data = request.content.read()
        request.setResponseCode(201)  # real code does this for brand-new files
        return self.add_data("URI:CHK:", data)

    def render_POST(self, request):
        t = request.args[u"t"][0]
        data = request.content.read()

        type_to_kind = {
            "mkdir-immutable": "URI:DIR2-CHK:"
        }
        kind = type_to_kind[t]
        return self.add_data(kind, data)

    def render_GET(self, request):
        uri = DecodedURL.from_text(request.uri.decode('utf8'))
        # XXX FIXME
        capability = uri.query[0][1]

        if self._data is None or capability not in self._data:
            return u"No data for '{}'".format(capability).decode("ascii")

        return self._data[capability]


def create_fake_tahoe_root():
    """
    :returns: an IResource instance that will handle certain Tahoe URI
        endpoints similar to a real Tahoe server.
    """
    root = _FakeTahoeRoot(
        uri=_FakeTahoeUriHandler(),
    )
    return root


@implementer(IBodyProducer)
class _SynchronousProducer(object):
    """
    A partial implementation of an :obj:`IBodyProducer` which produces its
    entire payload immediately.  There is no way to access to an instance of
    this object from :obj:`RequestTraversalAgent` or :obj:`StubTreq`, or even a
    :obj:`Resource: passed to :obj:`StubTreq`.

    This does not implement the :func:`IBodyProducer.stopProducing` method,
    because that is very difficult to trigger.  (The request from
    `RequestTraversalAgent` would have to be canceled while it is still in the
    transmitting state), and the intent is to use `RequestTraversalAgent` to
    make synchronous requests.
    """

    def __init__(self, body):
        """
        Create a synchronous producer with some bytes.
        """
        if isinstance(body, FileBodyProducer):
            body = body._inputFile.read()

        if not isinstance(body, bytes):
            raise ValueError(
                "'body' must be bytes not '{}'".format(type(body))
            )
        self.body = body
        self.length = len(body)

    def startProducing(self, consumer):
        """
        Immediately produce all data.
        """
        consumer.write(self.body)
        return succeed(None)


def create_tahoe_treq_client(root=None):
    """
    :param root: an instance created via `create_fake_tahoe_root`. The
        caller might want a copy of this to call `.add_data` for example.

    :returns: an instance of treq.client.HTTPClient wired up to
        in-memory fakes of the Tahoe WebUI. Only a subset of the real
        WebUI is available.
    """

    if root is None:
        root = create_fake_tahoe_root()

    client = HTTPClient(
        agent=RequestTraversalAgent(root),
        data_to_body_producer=_SynchronousProducer,
    )
    return client
