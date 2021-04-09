"""
A storage server plugin the test suite can use to validate the
functionality.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
from future.utils import native_str, native_str_to_bytes
from six import ensure_str

import attr

from zope.interface import (
    implementer,
)

from twisted.internet.defer import (
    succeed,
)
from twisted.web.resource import (
    Resource,
)
from twisted.web.static import (
    Data,
)
from foolscap.api import (
    RemoteInterface,
)

from allmydata.interfaces import (
    IFoolscapStoragePlugin,
    IStorageServer,
)
from allmydata.client import (
    AnnounceableStorageServer,
)
from allmydata.util.jsonbytes import (
    dumps,
)


class RIDummy(RemoteInterface):
    __remote_name__ = native_str("RIDummy.tahoe.allmydata.com")

    def just_some_method():
        """
        Just some method so there is something callable on this object.  We won't
        pretend to actually offer any storage capabilities.
        """


# type ignored due to missing stubs for Twisted
# https://twistedmatrix.com/trac/ticket/9717
@implementer(IFoolscapStoragePlugin)  # type: ignore
@attr.s
class DummyStorage(object):
    name = attr.ib()

    @property
    def _client_section_name(self):
        return u"storageclient.plugins.{}".format(self.name)

    def get_storage_server(self, configuration, get_anonymous_storage_server):
        if u"invalid" in configuration:
            raise Exception("The plugin is unhappy.")

        announcement = {u"value": configuration.get(u"some", u"default-value")}
        storage_server = DummyStorageServer(get_anonymous_storage_server)
        return succeed(
            AnnounceableStorageServer(
                announcement,
                storage_server,
            ),
        )

    def get_storage_client(self, configuration, announcement, get_rref):
        return DummyStorageClient(
            get_rref,
            dict(configuration.items(self._client_section_name, [])),
            announcement,
        )

    def get_client_resource(self, configuration):
        """
        :return: A static data resource that produces the given configuration when
            rendered, as an aid to testing.
        """
        items = configuration.items(self._client_section_name, [])
        resource = Data(
            native_str_to_bytes(dumps(dict(items))),
            ensure_str("text/json"),
        )
        # Give it some dynamic stuff too.
        resource.putChild(b"counter", GetCounter())
        return resource


class GetCounter(Resource, object):
    """
    ``GetCounter`` is a resource that returns a count of the number of times
    it has rendered a response to a GET request.

    :ivar int value: The number of ``GET`` requests rendered so far.
    """
    value = 0
    def render_GET(self, request):
        self.value += 1
        return native_str_to_bytes(dumps({"value": self.value}))


@implementer(RIDummy)
@attr.s(frozen=True)
class DummyStorageServer(object):  # type: ignore # warner/foolscap#78
    get_anonymous_storage_server = attr.ib()

    def remote_just_some_method(self):
        pass


@implementer(IStorageServer)
@attr.s
class DummyStorageClient(object):  # type: ignore # incomplete implementation
    get_rref = attr.ib()
    configuration = attr.ib()
    announcement = attr.ib()
