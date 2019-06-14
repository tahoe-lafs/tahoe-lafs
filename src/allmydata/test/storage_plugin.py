"""
A storage server plugin the test suite can use to validate the
functionality.
"""

import attr

from zope.interface import (
    implementer,
)

from foolscap.api import (
    RemoteInterface,
)

from allmydata.interfaces import (
    IFoolscapStoragePlugin,
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
        return AnnounceableStorageServer(
            announcement={u"value": configuration.get(u"some", u"default-value")},
            storage_server=DummyStorageServer(get_anonymous_storage_server),
        )


    def get_storage_client(self, configuration, announcement):
        pass



@implementer(RIDummy)
@attr.s(cmp=True, hash=True)
class DummyStorageServer(object):
    # TODO Requirement of some interface that instances be hashable
    get_anonymous_storage_server = attr.ib(cmp=False)

    def remote_just_some_method(self):
        pass
