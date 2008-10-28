
import os.path, stat
from cStringIO import StringIO
from zope.interface import implements
from twisted.internet import defer
from twisted.internet.interfaces import IPushProducer, IConsumer
from twisted.protocols import basic
from allmydata.interfaces import IFileNode, IFileURI, ICheckable
from allmydata.util import observer, log, base32
from allmydata.immutable.checker import SimpleCHKFileChecker, \
     SimpleCHKFileVerifier
from allmydata.immutable import download

class _ImmutableFileNodeBase(object):
    implements(IFileNode, ICheckable)

    def __init__(self, uri, client):
        self.u = IFileURI(uri)
        self._client = client

    def get_readonly_uri(self):
        return self.get_uri()

    def is_mutable(self):
        return False

    def is_readonly(self):
        return True

    def __hash__(self):
        return self.u.__hash__()
    def __eq__(self, other):
        if IFileNode.providedBy(other):
            return self.u.__eq__(other.u)
        else:
            return False
    def __ne__(self, other):
        if IFileNode.providedBy(other):
            return self.u.__eq__(other.u)
        else:
            return True

class PortionOfFile:
    # like a list slice (things[2:14]), but for a file on disk
    def __init__(self, fn, offset=0, size=None):
        self.f = open(fn, "rb")
        self.f.seek(offset)
        self.bytes_left = size

    def read(self, size=None):
        # bytes_to_read = min(size, self.bytes_left), but None>anything
        if size is None:
            bytes_to_read = self.bytes_left
        elif self.bytes_left is None:
            bytes_to_read = size
        else:
            bytes_to_read = min(size, self.bytes_left)
        data = self.f.read(bytes_to_read)
        if self.bytes_left is not None:
            self.bytes_left -= len(data)
        return data

class FileNode(_ImmutableFileNodeBase):
    checker_class = SimpleCHKFileChecker
    verifier_class = SimpleCHKFileVerifier

    def __init__(self, uri, client, cachefile):
        _ImmutableFileNodeBase.__init__(self, uri, client)
        self.cachefile = cachefile
        # five states:
        #  new FileNode, no downloads ever performed
        #  new FileNode, leftover file (partial)
        #  new FileNode, leftover file (whole)
        #  download in progress, not yet complete
        #  download complete
        self.download_in_progress = False
        self.fully_cached_observer = observer.OneShotObserverList()

    def get_uri(self):
        return self.u.to_string()

    def get_size(self):
        return self.u.get_size()

    def get_verifier(self):
        return self.u.get_verifier()

    def get_storage_index(self):
        return self.u.storage_index

    def check(self, monitor, verify=False):
        # TODO: pass the Monitor to SimpleCHKFileChecker or
        # SimpleCHKFileVerifier, have it call monitor.raise_if_cancelled()
        # before sending each request.
        storage_index = self.u.storage_index
        k = self.u.needed_shares
        N = self.u.total_shares
        size = self.u.size
        ueb_hash = self.u.uri_extension_hash
        if verify:
            v = self.verifier_class(self._client,
                                    storage_index, k, N, size, ueb_hash)
        else:
            v = self.checker_class(self._client, storage_index, k, N)
        return v.start()

    def check_and_repair(self, monitor, verify=False):
        # this is a stub, to allow the deep-check tests to pass.
        #raise NotImplementedError("not implemented yet")
        from allmydata.checker_results import CheckAndRepairResults
        cr = CheckAndRepairResults(self.u.storage_index)
        d = self.check(verify)
        def _done(r):
            cr.pre_repair_results = cr.post_repair_results = r
            cr.repair_attempted = False
            return cr
        d.addCallback(_done)
        return d

    def read(self, consumer, offset=0, size=None):
        if size is None:
            size = self.get_size() - offset

        assert self.cachefile

        try:
            filesize = os.stat(self.cachefile)[stat.ST_SIZE]
        except OSError:
            filesize = 0
        if filesize >= offset+size:
            log.msg(format=("immutable filenode read [%(si)s]: " +
                            "satisfied from cache " +
                            "(read %(start)d+%(size)d, filesize %(filesize)d)"),
                    si=base32.b2a(self.u.storage_index),
                    start=offset, size=size, filesize=filesize,
                    umid="5p5ECA", level=log.OPERATIONAL)
            f = PortionOfFile(self.cachefile, offset, size)
            d = basic.FileSender().beginFileTransfer(f, consumer)
            d.addCallback(lambda lastSent: consumer)
            return d

        if offset == 0 and size == self.get_size():
            # don't use the cache, just do a normal streaming download
            log.msg(format=("immutable filenode read [%(si)s]: " +
                            "doing normal full download"),
                    si=base32.b2a(self.u.storage_index),
                    umid="VRSBwg", level=log.OPERATIONAL)
            return self.download(download.ConsumerAdapter(consumer))

        if not self.download_in_progress:
            log.msg(format=("immutable filenode read [%(si)s]: " +
                            "starting download"),
                    si=base32.b2a(self.u.storage_index),
                    umid="h26Heg", level=log.OPERATIONAL)
            self.start_download_to_cache()

        # The file is being downloaded, but the portion we want isn't yet
        # available, so we have to wait. First cut: wait for the whole thing
        # to download. The second cut will be to wait for a specific range
        # milestone, with a download target that counts bytes and compares
        # them against a milestone list.
        log.msg(format=("immutable filenode read [%(si)s]: " +
                        "waiting for download"),
                si=base32.b2a(self.u.storage_index),
                umid="l48V7Q", level=log.OPERATIONAL)
        d = self.when_fully_cached()
        d.addCallback(lambda ignored: self.read(consumer, offset, size))
        return d

    def start_download_to_cache(self):
        assert not self.download_in_progress
        self.download_in_progress = True
        downloader = self._client.getServiceNamed("downloader")
        d = downloader.download_to_filename(self.get_uri(), self.cachefile)
        d.addBoth(self.fully_cached_observer.fire)

    def when_fully_cached(self):
        return self.fully_cached_observer.when_fired()


    def download(self, target):
        downloader = self._client.getServiceNamed("downloader")
        return downloader.download(self.get_uri(), target)

    def download_to_data(self):
        downloader = self._client.getServiceNamed("downloader")
        return downloader.download_to_data(self.get_uri())

class LiteralProducer:
    implements(IPushProducer)
    def resumeProducing(self):
        pass
    def stopProducing(self):
        pass


class LiteralFileNode(_ImmutableFileNodeBase):

    def __init__(self, uri, client):
        _ImmutableFileNodeBase.__init__(self, uri, client)

    def get_uri(self):
        return self.u.to_string()

    def get_size(self):
        return len(self.u.data)

    def get_verifier(self):
        return None

    def get_storage_index(self):
        return None

    def check(self, monitor, verify=False):
        return defer.succeed(None)

    def check_and_repair(self, monitor, verify=False):
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

    def download(self, target):
        # note that this does not update the stats_provider
        data = self.u.data
        if IConsumer.providedBy(target):
            target.registerProducer(LiteralProducer(), True)
        target.open(len(data))
        target.write(data)
        if IConsumer.providedBy(target):
            target.unregisterProducer()
        target.close()
        return defer.maybeDeferred(target.finish)

    def download_to_data(self):
        data = self.u.data
        return defer.succeed(data)
