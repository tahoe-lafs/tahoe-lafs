
import binascii
import copy
import time
now = time.time
from zope.interface import implements, Interface
from twisted.internet import defer
from twisted.internet.interfaces import IConsumer

from allmydata.interfaces import IImmutableFileNode, IUploadResults
from allmydata import uri
from allmydata.check_results import CheckResults, CheckAndRepairResults
from allmydata.util.dictutil import DictOfSets
from pycryptopp.cipher.aes import AES

# local imports
from allmydata.immutable.checker import Checker
from allmydata.immutable.repairer import Repairer
from allmydata.immutable.downloader.node import DownloadNode
from allmydata.immutable.downloader.status import DownloadStatus

class IDownloadStatusHandlingConsumer(Interface):
    def set_download_status_read_event(read_ev):
        """Record the DownloadStatus 'read event', to be updated with the
        time it takes to decrypt each chunk of data."""

class CiphertextFileNode:
    def __init__(self, verifycap, storage_broker, secret_holder,
                 terminator, history):
        assert isinstance(verifycap, uri.CHKFileVerifierURI)
        self._verifycap = verifycap
        self._storage_broker = storage_broker
        self._secret_holder = secret_holder
        self._terminator = terminator
        self._history = history
        self._download_status = None
        self._node = None # created lazily, on read()

    def _maybe_create_download_node(self):
        if not self._download_status:
            ds = DownloadStatus(self._verifycap.storage_index,
                                self._verifycap.size)
            if self._history:
                self._history.add_download(ds)
            self._download_status = ds
        if self._node is None:
            self._node = DownloadNode(self._verifycap, self._storage_broker,
                                      self._secret_holder,
                                      self._terminator,
                                      self._history, self._download_status)

    def read(self, consumer, offset=0, size=None):
        """I am the main entry point, from which FileNode.read() can get
        data. I feed the consumer with the desired range of ciphertext. I
        return a Deferred that fires (with the consumer) when the read is
        finished."""
        self._maybe_create_download_node()
        actual_size = size
        if actual_size is None:
            actual_size = self._verifycap.size - offset
        read_ev = self._download_status.add_read_event(offset, actual_size,
                                                       now())
        if IDownloadStatusHandlingConsumer.providedBy(consumer):
            consumer.set_download_status_read_event(read_ev)
        return self._node.read(consumer, offset, size, read_ev)

    def get_segment(self, segnum):
        """Begin downloading a segment. I return a tuple (d, c): 'd' is a
        Deferred that fires with (offset,data) when the desired segment is
        available, and c is an object on which c.cancel() can be called to
        disavow interest in the segment (after which 'd' will never fire).

        You probably need to know the segment size before calling this,
        unless you want the first few bytes of the file. If you ask for a
        segment number which turns out to be too large, the Deferred will
        errback with BadSegmentNumberError.

        The Deferred fires with the offset of the first byte of the data
        segment, so that you can call get_segment() before knowing the
        segment size, and still know which data you received.
        """
        self._maybe_create_download_node()
        return self._node.get_segment(segnum)

    def get_segment_size(self):
        # return a Deferred that fires with the file's real segment size
        self._maybe_create_download_node()
        return self._node.get_segsize()

    def get_storage_index(self):
        return self._verifycap.storage_index
    def get_verify_cap(self):
        return self._verifycap
    def get_size(self):
        return self._verifycap.size

    def raise_error(self):
        pass


    def check_and_repair(self, monitor, verify=False, add_lease=False):
        verifycap = self._verifycap
        storage_index = verifycap.storage_index
        sb = self._storage_broker
        servers = sb.get_connected_servers()
        sh = self._secret_holder

        c = Checker(verifycap=verifycap, servers=servers,
                    verify=verify, add_lease=add_lease, secret_holder=sh,
                    monitor=monitor)
        d = c.start()
        def _maybe_repair(cr):
            crr = CheckAndRepairResults(storage_index)
            crr.pre_repair_results = cr
            if cr.is_healthy():
                crr.post_repair_results = cr
                return defer.succeed(crr)
            else:
                crr.repair_attempted = True
                crr.repair_successful = False # until proven successful
                def _gather_repair_results(ur):
                    assert IUploadResults.providedBy(ur), ur
                    # clone the cr (check results) to form the basis of the
                    # prr (post-repair results)
                    prr = CheckResults(cr.uri, cr.storage_index)
                    prr.data = copy.deepcopy(cr.data)

                    sm = prr.data['sharemap']
                    assert isinstance(sm, DictOfSets), sm
                    sm.update(ur.sharemap)
                    servers_responding = set(prr.data['servers-responding'])
                    servers_responding.union(ur.sharemap.iterkeys())
                    prr.data['servers-responding'] = list(servers_responding)
                    prr.data['count-shares-good'] = len(sm)
                    prr.data['count-good-share-hosts'] = len(sm)
                    is_healthy = bool(len(sm) >= verifycap.total_shares)
                    is_recoverable = bool(len(sm) >= verifycap.needed_shares)
                    prr.set_healthy(is_healthy)
                    prr.set_recoverable(is_recoverable)
                    crr.repair_successful = is_healthy
                    prr.set_needs_rebalancing(len(sm) >= verifycap.total_shares)

                    crr.post_repair_results = prr
                    return crr
                def _repair_error(f):
                    # as with mutable repair, I'm not sure if I want to pass
                    # through a failure or not. TODO
                    crr.repair_successful = False
                    crr.repair_failure = f
                    return f
                r = Repairer(self, storage_broker=sb, secret_holder=sh,
                             monitor=monitor)
                d = r.start()
                d.addCallbacks(_gather_repair_results, _repair_error)
                return d

        d.addCallback(_maybe_repair)
        return d

    def check(self, monitor, verify=False, add_lease=False):
        verifycap = self._verifycap
        sb = self._storage_broker
        servers = sb.get_connected_servers()
        sh = self._secret_holder

        v = Checker(verifycap=verifycap, servers=servers,
                    verify=verify, add_lease=add_lease, secret_holder=sh,
                    monitor=monitor)
        return v.start()

class DecryptingConsumer:
    """I sit between a CiphertextDownloader (which acts as a Producer) and
    the real Consumer, decrypting everything that passes by. The real
    Consumer sees the real Producer, but the Producer sees us instead of the
    real consumer."""
    implements(IConsumer, IDownloadStatusHandlingConsumer)

    def __init__(self, consumer, readkey, offset):
        self._consumer = consumer
        self._read_event = None
        # TODO: pycryptopp CTR-mode needs random-access operations: I want
        # either a=AES(readkey, offset) or better yet both of:
        #  a=AES(readkey, offset=0)
        #  a.process(ciphertext, offset=xyz)
        # For now, we fake it with the existing iv= argument.
        offset_big = offset // 16
        offset_small = offset % 16
        iv = binascii.unhexlify("%032x" % offset_big)
        self._decryptor = AES(readkey, iv=iv)
        self._decryptor.process("\x00"*offset_small)

    def set_download_status_read_event(self, read_ev):
        self._read_event = read_ev

    def registerProducer(self, producer, streaming):
        # this passes through, so the real consumer can flow-control the real
        # producer. Therefore we don't need to provide any IPushProducer
        # methods. We implement all the IConsumer methods as pass-throughs,
        # and only intercept write() to perform decryption.
        self._consumer.registerProducer(producer, streaming)
    def unregisterProducer(self):
        self._consumer.unregisterProducer()
    def write(self, ciphertext):
        started = now()
        plaintext = self._decryptor.process(ciphertext)
        if self._read_event:
            elapsed = now() - started
            self._read_event.update(0, elapsed, 0)
        self._consumer.write(plaintext)

class ImmutableFileNode:
    implements(IImmutableFileNode)

    # I wrap a CiphertextFileNode with a decryption key
    def __init__(self, filecap, storage_broker, secret_holder, terminator,
                 history):
        assert isinstance(filecap, uri.CHKFileURI)
        verifycap = filecap.get_verify_cap()
        self._cnode = CiphertextFileNode(verifycap, storage_broker,
                                         secret_holder, terminator, history)
        assert isinstance(filecap, uri.CHKFileURI)
        self.u = filecap
        self._readkey = filecap.key

    # TODO: I'm not sure about this.. what's the use case for node==node? If
    # we keep it here, we should also put this on CiphertextFileNode
    def __hash__(self):
        return self.u.__hash__()
    def __eq__(self, other):
        if isinstance(other, ImmutableFileNode):
            return self.u.__eq__(other.u)
        else:
            return False
    def __ne__(self, other):
        if isinstance(other, ImmutableFileNode):
            return self.u.__eq__(other.u)
        else:
            return True

    def read(self, consumer, offset=0, size=None):
        decryptor = DecryptingConsumer(consumer, self._readkey, offset)
        d = self._cnode.read(decryptor, offset, size)
        d.addCallback(lambda dc: consumer)
        return d

    def raise_error(self):
        pass

    def get_write_uri(self):
        return None

    def get_readonly_uri(self):
        return self.get_uri()

    def get_uri(self):
        return self.u.to_string()
    def get_cap(self):
        return self.u
    def get_readcap(self):
        return self.u.get_readonly()
    def get_verify_cap(self):
        return self.u.get_verify_cap()
    def get_repair_cap(self):
        # CHK files can be repaired with just the verifycap
        return self.u.get_verify_cap()

    def get_storage_index(self):
        return self.u.get_storage_index()

    def get_size(self):
        return self.u.get_size()
    def get_current_size(self):
        return defer.succeed(self.get_size())

    def is_mutable(self):
        return False

    def is_readonly(self):
        return True

    def is_unknown(self):
        return False

    def is_allowed_in_immutable_directory(self):
        return True

    def check_and_repair(self, monitor, verify=False, add_lease=False):
        return self._cnode.check_and_repair(monitor, verify, add_lease)
    def check(self, monitor, verify=False, add_lease=False):
        return self._cnode.check(monitor, verify, add_lease)
