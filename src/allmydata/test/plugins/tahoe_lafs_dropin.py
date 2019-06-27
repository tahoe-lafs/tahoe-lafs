from allmydata.test.common import (
    AdoptedServerPort,
)

from allmydata.test.storage_plugin import (
    DummyStorage,
)

adoptedEndpointParser = AdoptedServerPort()

dummyStoragev1 = DummyStorage(u"tahoe-lafs-dummy-v1")
dummyStoragev2 = DummyStorage(u"tahoe-lafs-dummy-v2")
