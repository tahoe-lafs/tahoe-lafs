from cStringIO import StringIO
from zope.interface import implements
from twisted.internet import defer
from twisted.internet.interfaces import IPushProducer
from twisted.protocols import basic
from allmydata.interfaces import IImmutableFileNode, ICheckable
from allmydata.uri import LiteralFileURI

class _ImmutableFileNodeBase(object):
    implements(IImmutableFileNode, ICheckable)

    def get_write_uri(self):
        return None

    def get_readonly_uri(self):
        return self.get_uri()

    def is_mutable(self):
        return False

    def is_readonly(self):
        return True

    def is_unknown(self):
        return False

    def is_allowed_in_immutable_directory(self):
        return True

    def raise_error(self):
        pass

    def __hash__(self):
        return self.u.__hash__()
    def __eq__(self, other):
        if isinstance(other, _ImmutableFileNodeBase):
            return self.u.__eq__(other.u)
        else:
            return False
    def __ne__(self, other):
        if isinstance(other, _ImmutableFileNodeBase):
            return self.u.__eq__(other.u)
        else:
            return True


class LiteralProducer:
    implements(IPushProducer)
    def resumeProducing(self):
        pass
    def stopProducing(self):
        pass


class LiteralFileNode(_ImmutableFileNodeBase):

    def __init__(self, filecap):
        assert isinstance(filecap, LiteralFileURI)
        self.u = filecap

    def get_size(self):
        return len(self.u.data)
    def get_current_size(self):
        return defer.succeed(self.get_size())

    def get_cap(self):
        return self.u
    def get_readcap(self):
        return self.u
    def get_verify_cap(self):
        return None
    def get_repair_cap(self):
        return None

    def get_uri(self):
        return self.u.to_string()

    def get_storage_index(self):
        return None

    def check(self, monitor, verify=False, add_lease=False):
        return defer.succeed(None)

    def check_and_repair(self, monitor, verify=False, add_lease=False):
        return defer.succeed(None)

    def read(self, consumer, offset=0, size=None):
        if size is None:
            data = self.u.data[offset:]
        else:
            data = self.u.data[offset:offset+size]

        # We use twisted.protocols.basic.FileSender, which only does
        # non-streaming, i.e. PullProducer, where the receiver/consumer must
        # ask explicitly for each chunk of data. There are only two places in
        # the Twisted codebase that can't handle streaming=False, both of
        # which are in the upload path for an FTP/SFTP server
        # (protocols.ftp.FileConsumer and
        # vfs.adapters.ftp._FileToConsumerAdapter), neither of which is
        # likely to be used as the target for a Tahoe download.

        d = basic.FileSender().beginFileTransfer(StringIO(data), consumer)
        d.addCallback(lambda lastSent: consumer)
        return d
