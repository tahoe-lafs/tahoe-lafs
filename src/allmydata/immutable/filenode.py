"""
Ported to Python 3.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from functools import reduce
import binascii
from time import time as now

from zope.interface import implementer
from twisted.internet import defer

from allmydata import uri
from twisted.internet.interfaces import IConsumer
from allmydata.crypto import aes
from allmydata.interfaces import IImmutableFileNode, IUploadResults
from allmydata.util import consumer
from allmydata.check_results import CheckResults, CheckAndRepairResults
from allmydata.util.dictutil import DictOfSets
from allmydata.util.happinessutil import servers_of_happiness

# local imports
from allmydata.immutable.checker import Checker
from allmydata.immutable.repairer import Repairer
from allmydata.immutable.downloader.node import DownloadNode, \
     IDownloadStatusHandlingConsumer
from allmydata.immutable.downloader.status import DownloadStatus

class CiphertextFileNode(object):
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
        return self._node.read(consumer, offset, size)

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

    def is_mutable(self):
        return False

    def check_and_repair(self, monitor, verify=False, add_lease=False):
        c = Checker(verifycap=self._verifycap,
                    servers=self._storage_broker.get_connected_servers(),
                    verify=verify, add_lease=add_lease,
                    secret_holder=self._secret_holder,
                    monitor=monitor)
        d = c.start()
        d.addCallback(self._maybe_repair, monitor)
        return d

    def _maybe_repair(self, cr, monitor):
        crr = CheckAndRepairResults(self._verifycap.storage_index)
        crr.pre_repair_results = cr
        if cr.is_healthy():
            crr.post_repair_results = cr
            return defer.succeed(crr)

        crr.repair_attempted = True
        crr.repair_successful = False # until proven successful
        def _repair_error(f):
            # as with mutable repair, I'm not sure if I want to pass
            # through a failure or not. TODO
            crr.repair_successful = False
            crr.repair_failure = f
            return f
        r = Repairer(self, storage_broker=self._storage_broker,
                     secret_holder=self._secret_holder,
                     monitor=monitor)
        d = r.start()
        d.addCallbacks(self._gather_repair_results, _repair_error,
                       callbackArgs=(cr, crr,))
        return d

    def _gather_repair_results(self, ur, cr, crr):
        assert IUploadResults.providedBy(ur), ur
        # clone the cr (check results) to form the basis of the
        # prr (post-repair results)

        verifycap = self._verifycap
        servers_responding = set(cr.get_servers_responding())
        sm = DictOfSets()
        assert isinstance(cr.get_sharemap(), DictOfSets)
        for shnum, servers in cr.get_sharemap().items():
            for server in servers:
                sm.add(shnum, server)
        for shnum, servers in ur.get_sharemap().items():
            for server in servers:
                sm.add(shnum, server)
                servers_responding.add(server)

        good_hosts = len(reduce(set.union, sm.values(), set()))
        is_healthy = bool(len(sm) >= verifycap.total_shares)
        is_recoverable = bool(len(sm) >= verifycap.needed_shares)

        count_happiness = servers_of_happiness(sm)

        prr = CheckResults(cr.get_uri(), cr.get_storage_index(),
                           healthy=is_healthy, recoverable=is_recoverable,
                           count_happiness=count_happiness,
                           count_shares_needed=verifycap.needed_shares,
                           count_shares_expected=verifycap.total_shares,
                           count_shares_good=len(sm),
                           count_good_share_hosts=good_hosts,
                           count_recoverable_versions=int(is_recoverable),
                           count_unrecoverable_versions=int(not is_recoverable),
                           servers_responding=list(servers_responding),
                           sharemap=sm,
                           count_wrong_shares=0, # no such thing as wrong, for immutable
                           list_corrupt_shares=cr.get_corrupt_shares(),
                           count_corrupt_shares=len(cr.get_corrupt_shares()),
                           list_incompatible_shares=cr.get_incompatible_shares(),
                           count_incompatible_shares=len(cr.get_incompatible_shares()),
                           summary="",
                           report=[],
                           share_problems=[],
                           servermap=None)
        crr.repair_successful = is_healthy
        crr.post_repair_results = prr
        return crr

    def check(self, monitor, verify=False, add_lease=False):
        verifycap = self._verifycap
        sb = self._storage_broker
        servers = sb.get_connected_servers()
        sh = self._secret_holder

        v = Checker(verifycap=verifycap, servers=servers,
                    verify=verify, add_lease=add_lease, secret_holder=sh,
                    monitor=monitor)
        return v.start()

@implementer(IConsumer, IDownloadStatusHandlingConsumer)
class DecryptingConsumer(object):
    """I sit between a CiphertextDownloader (which acts as a Producer) and
    the real Consumer, decrypting everything that passes by. The real
    Consumer sees the real Producer, but the Producer sees us instead of the
    real consumer."""

    def __init__(self, consumer, readkey, offset):
        self._consumer = consumer
        self._read_ev = None
        self._download_status = None
        # TODO: pycryptopp CTR-mode needs random-access operations: I want
        # either a=AES(readkey, offset) or better yet both of:
        #  a=AES(readkey, offset=0)
        #  a.process(ciphertext, offset=xyz)
        # For now, we fake it with the existing iv= argument.
        offset_big = offset // 16
        offset_small = offset % 16
        iv = binascii.unhexlify("%032x" % offset_big)
        self._decryptor = aes.create_decryptor(readkey, iv)
        # this is just to advance the counter
        aes.decrypt_data(self._decryptor, b"\x00" * offset_small)

    def set_download_status_read_event(self, read_ev):
        self._read_ev = read_ev
    def set_download_status(self, ds):
        self._download_status = ds

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
        plaintext = aes.decrypt_data(self._decryptor, ciphertext)
        if self._read_ev:
            elapsed = now() - started
            self._read_ev.update(0, elapsed, 0)
        if self._download_status:
            self._download_status.add_misc_event("AES", started, now())
        self._consumer.write(plaintext)

@implementer(IImmutableFileNode)
class ImmutableFileNode(object):

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

    def get_best_readable_version(self):
        """
        Return an IReadable of the best version of this file. Since
        immutable files can have only one version, we just return the
        current filenode.
        """
        return defer.succeed(self)

    def download_best_version(self, progress=None):
        """
        Download the best version of this file, returning its contents
        as a bytestring. Since there is only one version of an immutable
        file, we download and return the contents of this file.
        """
        d = consumer.download_to_data(self, progress=progress)
        return d

    # for an immutable file, download_to_data (specified in IReadable)
    # is the same as download_best_version (specified in IFileNode). For
    # mutable files, the difference is more meaningful, since they can
    # have multiple versions.
    download_to_data = download_best_version


    # get_size() (IReadable), get_current_size() (IFilesystemNode), and
    # get_size_of_best_version(IFileNode) are all the same for immutable
    # files.
    get_size_of_best_version = get_current_size
