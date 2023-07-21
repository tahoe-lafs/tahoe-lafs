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

from __future__ import annotations

import hashlib

import attr

from hyperlink import DecodedURL

from twisted.web.resource import (
    Resource,
)
from twisted.web.iweb import (
    IBodyProducer,
)
from twisted.web import (
    http,
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
from ..util.dictutil import BytesKeyDict


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
        fresh, cap = self._uri.add_data(kind, data)
        return cap


KNOWN_CAPABILITIES = [
    getattr(allmydata.uri, t).BASE_STRING
    for t in dir(allmydata.uri)
    if hasattr(getattr(allmydata.uri, t), 'BASE_STRING')
]


def capability_generator(kind):
    """
    Deterministically generates a stream of valid capabilities of the
    given kind. The N, K and size values aren't related to anything
    real.

    :param bytes kind: the kind of capability, like `URI:CHK`

    :returns: a generator that yields new capablities of a particular
        kind.
    """
    if not isinstance(kind, bytes):
        raise TypeError("'kind' must be bytes")

    if kind not in KNOWN_CAPABILITIES:
        raise ValueError(
            "Unknown capability kind '{}' (valid are {})".format(
                kind.decode('ascii'),
                ", ".join([x.decode('ascii') for x in KNOWN_CAPABILITIES]),
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
        key_hasher.update(b"\x00")
        ueb_hasher.update(b"\x00")

        key = base32.b2a(key_hasher.digest()[:16])  # key is 16 bytes
        ueb_hash = base32.b2a(ueb_hasher.digest())  # ueb hash is 32 bytes

        cap = u"{kind}{key}:{ueb_hash}:{n}:{k}:{size}".format(
            kind=kind.decode('ascii'),
            key=key.decode('ascii'),
            ueb_hash=ueb_hash.decode('ascii'),
            n=1,
            k=1,
            size=number * 1000,
        )
        yield cap.encode("ascii")


@attr.s
class _FakeTahoeUriHandler(Resource, object):
    """
    An in-memory fake of (some of) the `/uri` endpoint of a Tahoe
    WebUI
    """

    isLeaf = True

    data: BytesKeyDict = attr.ib(default=attr.Factory(BytesKeyDict))
    capability_generators = attr.ib(default=attr.Factory(dict))

    def _generate_capability(self, kind):
        """
        :param str kind: any valid capability-string type

        :returns: the next capability-string for the given kind
        """
        if kind not in self.capability_generators:
            self.capability_generators[kind] = capability_generator(kind)
        capability = next(self.capability_generators[kind])
        return capability

    def add_data(self, kind, data):
        """
        adds some data to our grid

        :returns: a two-tuple: a bool (True if the data is freshly added) and a capability-string
        """
        if not isinstance(kind, bytes):
            raise TypeError("'kind' must be bytes")
        if not isinstance(data, bytes):
            raise TypeError("'data' must be bytes")

        for k in self.data:
            if self.data[k] == data:
                return (False, k)

        cap = self._generate_capability(kind)
        # it should be impossible for this to already be in our data,
        # but check anyway to be sure
        if cap in self.data:
            raise Exception("Internal error; key already exists somehow")
        self.data[cap] = data
        return (True, cap)

    def render_PUT(self, request):
        data = request.content.read()
        fresh, cap = self.add_data(b"URI:CHK:", data)
        if fresh:
            request.setResponseCode(http.CREATED)  # real code does this for brand-new files
        else:
            request.setResponseCode(http.OK)  # replaced/modified files
        return cap

    def render_POST(self, request):
        t = request.args[u"t"][0]
        data = request.content.read()

        type_to_kind = {
            "mkdir-immutable": b"URI:DIR2-CHK:"
        }
        kind = type_to_kind[t]
        fresh, cap = self.add_data(kind, data)
        return cap

    def render_GET(self, request):
        uri = DecodedURL.from_text(request.uri.decode('utf8'))
        capability = None
        for arg, value in uri.query:
            if arg == u"uri":
                capability = value.encode("utf-8")
        # it's legal to use the form "/uri/<capability>"
        if capability is None and request.postpath and request.postpath[0]:
            capability = request.postpath[0]

        # if we don't yet have a capability, that's an error
        if capability is None:
            request.setResponseCode(http.BAD_REQUEST)
            return b"GET /uri requires uri="

        # the user gave us a capability; if our Grid doesn't have any
        # data for it, that's an error.
        if capability not in self.data:
            request.setResponseCode(http.GONE)
            return u"No data for '{}'".format(capability.decode('ascii')).encode("utf-8")

        return self.data[capability]


def create_fake_tahoe_root():
    """
    If you wish to pre-populate data into the fake Tahoe grid, retain
    a reference to this root by creating it yourself and passing it to
    `create_tahoe_treq_client`. For example::

        root = create_fake_tahoe_root()
        cap_string = root.add_data(...)
        client = create_tahoe_treq_client(root)

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

    def stopProducing(self):
        pass

    def pauseProducing(self):
        pass

    def resumeProducing(self):
        pass


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
