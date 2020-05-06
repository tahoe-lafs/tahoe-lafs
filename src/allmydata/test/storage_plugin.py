"""
A storage server plugin the test suite can use to validate the
functionality.
"""

from json import (
    dumps,
)

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


class RIDummy(RemoteInterface):
    __remote_name__ = "RIDummy.tahoe.allmydata.com"

    def just_some_method():
        """
        Just some method so there is something callable on this object.  We won't
        pretend to actually offer any storage capabilities.
        """



@implementer(IFoolscapStoragePlugin)
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
            dumps(dict(items)),
            b"text/json",
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
        return dumps({"value": self.value})


@implementer(RIDummy)
@attr.s(frozen=True)
class DummyStorageServer(object):
    get_anonymous_storage_server = attr.ib()

    def remote_just_some_method(self):
        pass


@implementer(IStorageServer)
@attr.s
class DummyStorageClient(object):
    get_rref = attr.ib()
    configuration = attr.ib()
    announcement = attr.ib()
