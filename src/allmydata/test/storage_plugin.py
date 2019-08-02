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
        return DummyStorageClient(get_rref, configuration, announcement)

    def get_client_resource(self, configuration):
        """
        :return: A static data resource that produces the given configuration when
            rendered, as an aid to testing.
        """
        return Data(dumps(configuration), b"text/json")



@implementer(RIDummy)
@attr.s(cmp=True, hash=True)
class DummyStorageServer(object):
    # TODO Requirement of some interface that instances be hashable
    get_anonymous_storage_server = attr.ib(cmp=False)

    def remote_just_some_method(self):
        pass


@implementer(IStorageServer)
@attr.s
class DummyStorageClient(object):
    get_rref = attr.ib()
    configuration = attr.ib()
    announcement = attr.ib()
