import io
import os
import string

import attr

from twisted.internet import defer
from twisted.python.filepath import (
    FilePath,
)
from twisted.web.resource import (
    Resource,
)
from twisted.web.client import (
    Agent,
    FileBodyProducer,
)

from treq.client import (
    HTTPClient,
)
from treq.testing import (
    RequestTraversalAgent,
    RequestSequence,
    StubTreq,
)


class _FakeTahoeRoot(Resource):
    """
    This is a sketch of how an in-memory 'fake' of a Tahoe
    WebUI. Ultimately, this will live in Tahoe
    """

    def __init__(self, uri=None):
        Resource.__init__(self)  # this is an old-style class :(
        self._uri = uri
        self.putChild(b"uri", self._uri)

    def add_data(self, key, data):
        return self._uri.add_data(key, data)

@attr.s
class _FakeCapability(object):
    """
    """
    data=attr.ib()


# XXX want to make all kinds of caps, like
# URI:CHK:... URI:DIR2:... etc

import allmydata.uri
KNOWN_CAPABILITIES = [
    getattr(allmydata.uri, t).BASE_STRING
    for t in dir(allmydata.uri)
    if hasattr(getattr(allmydata.uri, t), 'BASE_STRING')
]


from allmydata.immutable.upload import BaseUploadable
from allmydata.interfaces import IUploadable
from zope.interface import implementer
from twisted.internet.defer import (
    inlineCallbacks,
    succeed,
    returnValue,
)


def deterministic_key_generator():
    character = 0
    while character < (26 * 2):
        key = string.letters[character] * 16
        character += 1
        yield key
    raise RuntimeError("Ran out of keys")


@implementer(IUploadable)
class DataUploadable(BaseUploadable):
    # Base gives us:
    # set_upload_status
    # set_default_encoding_parameters
    # get_all_encoding_parameters

    def __init__(self, data, key=None):
        self._data = data
        self._where = 0
        self._key = key if key is not None else urandom(16)

    def get_encryption_key(self):
        return succeed(self._key)

    def get_size(self):
        return succeed(len(self._data))

    @inlineCallbacks
    def read(self, amount):
        data = [self._data[self._where : self._where + amount]]
        self._where += amount
        yield
        returnValue(data)

    def close(self):
        pass

@inlineCallbacks
def create_fake_capability(kind, key, data):
    if kind not in KNOWN_CAPABILITIES:
        raise ValueError(
            "'{}' not a known kind: {}".format(
                kind,
                ", ".join(list(KNOWN_CAPABILITIES.keys())),
            )
        )

    # XXX to use a allmydata.immutable.upload.CHKUploader directly,
    # we'd need to instantiate:

    from allmydata.immutable.upload import (
        CHKUploader,
        EncryptAnUploadable,
    )
    from allmydata.immutable.encode import (
        Encoder,
    )

    class _FakeSecretHolder(object):
        def get_renewal_secret(self):
            return "renewal_secret"

        def get_cancel_secret(self):
            return "cancel_secret"


    @attr.s
    class _FakeBucket(object):
        data = attr.ib(init=False, default="")

        def callRemoteOnly(self, *args):
            pass  # print("callRemoteOnly({})".format(args))

        def callRemote(self, verb, *args):
            if verb == 'write':
                offset, data = args
                assert offset >= len(self.data)
                while offset > len(self.data):
                    self.data += 'X'  # marker data; we're padding
                self.data += data
            elif verb == 'close':
                pass
            else:
                print("callRemote({})".format(args))


    @attr.s
    class _FakeStorageServer(object):
        buckets = attr.ib(default=attr.Factory(lambda: [_FakeBucket()]))

        def get_buckets(self, storage_index):
            return succeed(self.buckets)

        def allocate_buckets(self, storage_index, renew_secret, cancel_secret, sharenums, allocated_size, canary=None):
            # returns a 2-tuple .. second one maps share-num to BucketWriter
            return succeed((
                {},
                {
                    i: bucket
                    for i, bucket in enumerate(self.buckets)
                }
            ))

    class _FakeServer(object):
        def get_serverid(self):
            return "fake_server"

        def get_name(self):
            return "steven"

        def get_version(self):
            return {
                "http://allmydata.org/tahoe/protocols/storage/v1": {
                    "maximum-immutable-share-size": 10*1024*1024*1024,
                }
            }

        def get_lease_seed(self):
            return "decafbaddecafbaddeca"

        def get_storage_server(self):
            return _FakeStorageServer()


    class _FakeStorageBroker(object):
        def get_servers_for_psi(self, storage_index):
            return [_FakeServer()]

    storage_broker = _FakeStorageBroker()
    secret_holder = _FakeSecretHolder()
    uploader = CHKUploader(storage_broker, secret_holder, progress=None, reactor=None)
    uploadable = DataUploadable(data, key=key)
    uploadable.set_default_encoding_parameters({
        "n": 1,
        "k": 1,
        "happy": 1,
    })
    encrypted_uploadable = EncryptAnUploadable(uploadable)

    encoder = Encoder()
    yield encoder.set_encrypted_uploadable(encrypted_uploadable)

    uploadresults = yield uploader.start(encrypted_uploadable)

    enc_key = yield uploadable.get_encryption_key()

    from allmydata.uri import from_string as uri_from_string
    from allmydata.uri import CHKFileURI

    verify_cap = uri_from_string(uploadresults.get_verifycapstr())
    read_cap = CHKFileURI(
        enc_key,
        verify_cap.uri_extension_hash,
        verify_cap.needed_shares,
        verify_cap.total_shares,
        verify_cap.size,
    )
    uploadresults.set_uri(read_cap.to_string())

    returnValue(read_cap)


class _FakeTahoeUriHandler(Resource):
    """
    """

    isLeaf = True

    @inlineCallbacks
    def add_data(self, key, data):
        """
        adds some data to our grid, returning a capability
        """
        cap = yield create_fake_capability("URI:CHK:", key, data)
        returnValue(cap)

    def render_GET(self, request):
        print(request)
        print(request.uri)
        return b"URI:DIR2-CHK:some capability"


@inlineCallbacks
def create_fake_tahoe_root():
    """
    Probably should take some params to control what this fake does:
    return errors, pre-populate capabilities, ...
    """
    root = _FakeTahoeRoot(
        uri=_FakeTahoeUriHandler(),
    )
    yield
    returnValue(root)
