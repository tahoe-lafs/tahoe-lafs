
import struct, time
from itertools import count
from zope.interface import implements
from twisted.internet import defer
from twisted.python import failure
from foolscap import DeadReferenceError
from foolscap.eventual import eventually, fireEventually
from allmydata.interfaces import IRetrieveStatus, NotEnoughSharesError
from allmydata.util import hashutil, idlib, log
from allmydata import hashtree, codec
from allmydata.storage.server import si_b2a
from pycryptopp.cipher.aes import AES
from pycryptopp.publickey import rsa

from common import DictOfSets, CorruptShareError, UncoordinatedWriteError
from layout import SIGNED_PREFIX, unpack_share_data

class RetrieveStatus:
    implements(IRetrieveStatus)
    statusid_counter = count(0)
    def __init__(self):
        self.timings = {}
        self.timings["fetch_per_server"] = {}
        self.timings["cumulative_verify"] = 0.0
        self.problems = {}
        self.active = True
        self.storage_index = None
        self.helper = False
        self.encoding = ("?","?")
        self.size = None
        self.status = "Not started"
        self.progress = 0.0
        self.counter = self.statusid_counter.next()
        self.started = time.time()

    def get_started(self):
        return self.started
    def get_storage_index(self):
        return self.storage_index
    def get_encoding(self):
        return self.encoding
    def using_helper(self):
        return self.helper
    def get_size(self):
        return self.size
    def get_status(self):
        return self.status
    def get_progress(self):
        return self.progress
    def get_active(self):
        return self.active
    def get_counter(self):
        return self.counter

    def add_fetch_timing(self, peerid, elapsed):
        if peerid not in self.timings["fetch_per_server"]:
            self.timings["fetch_per_server"][peerid] = []
        self.timings["fetch_per_server"][peerid].append(elapsed)
    def set_storage_index(self, si):
        self.storage_index = si
    def set_helper(self, helper):
        self.helper = helper
    def set_encoding(self, k, n):
        self.encoding = (k, n)
    def set_size(self, size):
        self.size = size
    def set_status(self, status):
        self.status = status
    def set_progress(self, value):
        self.progress = value
    def set_active(self, value):
        self.active = value

class Marker:
    pass

class Retrieve:
    # this class is currently single-use. Eventually (in MDMF) we will make
    # it multi-use, in which case you can call download(range) multiple
    # times, and each will have a separate response chain. However the
    # Retrieve object will remain tied to a specific version of the file, and
    # will use a single ServerMap instance.

    def __init__(self, filenode, servermap, verinfo, fetch_privkey=False):
        self._node = filenode
        assert self._node._pubkey
        self._storage_index = filenode.get_storage_index()
        assert self._node._readkey
        self._last_failure = None
        prefix = si_b2a(self._storage_index)[:5]
        self._log_number = log.msg("Retrieve(%s): starting" % prefix)
        self._outstanding_queries = {} # maps (peerid,shnum) to start_time
        self._running = True
        self._decoding = False
        self._bad_shares = set()

        self.servermap = servermap
        assert self._node._pubkey
        self.verinfo = verinfo
        # during repair, we may be called upon to grab the private key, since
        # it wasn't picked up during a verify=False checker run, and we'll
        # need it for repair to generate the a new version.
        self._need_privkey = fetch_privkey
        if self._node._privkey:
            self._need_privkey = False

        self._status = RetrieveStatus()
        self._status.set_storage_index(self._storage_index)
        self._status.set_helper(False)
        self._status.set_progress(0.0)
        self._status.set_active(True)
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.verinfo
        self._status.set_size(datalength)
        self._status.set_encoding(k, N)

    def get_status(self):
        return self._status

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.mutable.retrieve"
        return log.msg(*args, **kwargs)

    def download(self):
        self._done_deferred = defer.Deferred()
        self._started = time.time()
        self._status.set_status("Retrieving Shares")

        # first, which servers can we use?
        versionmap = self.servermap.make_versionmap()
        shares = versionmap[self.verinfo]
        # this sharemap is consumed as we decide to send requests
        self.remaining_sharemap = DictOfSets()
        for (shnum, peerid, timestamp) in shares:
            self.remaining_sharemap.add(shnum, peerid)

        self.shares = {} # maps shnum to validated blocks

        # how many shares do we need?
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.verinfo
        assert len(self.remaining_sharemap) >= k
        # we start with the lowest shnums we have available, since FEC is
        # faster if we're using "primary shares"
        self.active_shnums = set(sorted(self.remaining_sharemap.keys())[:k])
        for shnum in self.active_shnums:
            # we use an arbitrary peer who has the share. If shares are
            # doubled up (more than one share per peer), we could make this
            # run faster by spreading the load among multiple peers. But the
            # algorithm to do that is more complicated than I want to write
            # right now, and a well-provisioned grid shouldn't have multiple
            # shares per peer.
            peerid = list(self.remaining_sharemap[shnum])[0]
            self.get_data(shnum, peerid)

        # control flow beyond this point: state machine. Receiving responses
        # from queries is the input. We might send out more queries, or we
        # might produce a result.

        return self._done_deferred

    def get_data(self, shnum, peerid):
        self.log(format="sending sh#%(shnum)d request to [%(peerid)s]",
                 shnum=shnum,
                 peerid=idlib.shortnodeid_b2a(peerid),
                 level=log.NOISY)
        ss = self.servermap.connections[peerid]
        started = time.time()
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.verinfo
        offsets = dict(offsets_tuple)

        # we read the checkstring, to make sure that the data we grab is from
        # the right version.
        readv = [ (0, struct.calcsize(SIGNED_PREFIX)) ]

        # We also read the data, and the hashes necessary to validate them
        # (share_hash_chain, block_hash_tree, share_data). We don't read the
        # signature or the pubkey, since that was handled during the
        # servermap phase, and we'll be comparing the share hash chain
        # against the roothash that was validated back then.

        readv.append( (offsets['share_hash_chain'],
                       offsets['enc_privkey'] - offsets['share_hash_chain'] ) )

        # if we need the private key (for repair), we also fetch that
        if self._need_privkey:
            readv.append( (offsets['enc_privkey'],
                           offsets['EOF'] - offsets['enc_privkey']) )

        m = Marker()
        self._outstanding_queries[m] = (peerid, shnum, started)

        # ask the cache first
        got_from_cache = False
        datavs = []
        for (offset, length) in readv:
            (data, timestamp) = self._node._cache.read(self.verinfo, shnum,
                                                       offset, length)
            if data is not None:
                datavs.append(data)
        if len(datavs) == len(readv):
            self.log("got data from cache")
            got_from_cache = True
            d = fireEventually({shnum: datavs})
            # datavs is a dict mapping shnum to a pair of strings
        else:
            d = self._do_read(ss, peerid, self._storage_index, [shnum], readv)
        self.remaining_sharemap.discard(shnum, peerid)

        d.addCallback(self._got_results, m, peerid, started, got_from_cache)
        d.addErrback(self._query_failed, m, peerid)
        # errors that aren't handled by _query_failed (and errors caused by
        # _query_failed) get logged, but we still want to check for doneness.
        def _oops(f):
            self.log(format="problem in _query_failed for sh#%(shnum)d to %(peerid)s",
                     shnum=shnum,
                     peerid=idlib.shortnodeid_b2a(peerid),
                     failure=f,
                     level=log.WEIRD, umid="W0xnQA")
        d.addErrback(_oops)
        d.addBoth(self._check_for_done)
        # any error during _check_for_done means the download fails. If the
        # download is successful, _check_for_done will fire _done by itself.
        d.addErrback(self._done)
        d.addErrback(log.err)
        return d # purely for testing convenience

    def _do_read(self, ss, peerid, storage_index, shnums, readv):
        # isolate the callRemote to a separate method, so tests can subclass
        # Publish and override it
        d = ss.callRemote("slot_readv", storage_index, shnums, readv)
        return d

    def remove_peer(self, peerid):
        for shnum in list(self.remaining_sharemap.keys()):
            self.remaining_sharemap.discard(shnum, peerid)

    def _got_results(self, datavs, marker, peerid, started, got_from_cache):
        now = time.time()
        elapsed = now - started
        if not got_from_cache:
            self._status.add_fetch_timing(peerid, elapsed)
        self.log(format="got results (%(shares)d shares) from [%(peerid)s]",
                 shares=len(datavs),
                 peerid=idlib.shortnodeid_b2a(peerid),
                 level=log.NOISY)
        self._outstanding_queries.pop(marker, None)
        if not self._running:
            return

        # note that we only ask for a single share per query, so we only
        # expect a single share back. On the other hand, we use the extra
        # shares if we get them.. seems better than an assert().

        for shnum,datav in datavs.items():
            (prefix, hash_and_data) = datav[:2]
            try:
                self._got_results_one_share(shnum, peerid,
                                            prefix, hash_and_data)
            except CorruptShareError, e:
                # log it and give the other shares a chance to be processed
                f = failure.Failure()
                self.log(format="bad share: %(f_value)s",
                         f_value=str(f.value), failure=f,
                         level=log.WEIRD, umid="7fzWZw")
                self.notify_server_corruption(peerid, shnum, str(e))
                self.remove_peer(peerid)
                self.servermap.mark_bad_share(peerid, shnum, prefix)
                self._bad_shares.add( (peerid, shnum) )
                self._status.problems[peerid] = f
                self._last_failure = f
                pass
            if self._need_privkey and len(datav) > 2:
                lp = None
                self._try_to_validate_privkey(datav[2], peerid, shnum, lp)
        # all done!

    def notify_server_corruption(self, peerid, shnum, reason):
        ss = self.servermap.connections[peerid]
        ss.callRemoteOnly("advise_corrupt_share",
                          "mutable", self._storage_index, shnum, reason)

    def _got_results_one_share(self, shnum, peerid,
                               got_prefix, got_hash_and_data):
        self.log("_got_results: got shnum #%d from peerid %s"
                 % (shnum, idlib.shortnodeid_b2a(peerid)))
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.verinfo
        assert len(got_prefix) == len(prefix), (len(got_prefix), len(prefix))
        if got_prefix != prefix:
            msg = "someone wrote to the data since we read the servermap: prefix changed"
            raise UncoordinatedWriteError(msg)
        (share_hash_chain, block_hash_tree,
         share_data) = unpack_share_data(self.verinfo, got_hash_and_data)

        assert isinstance(share_data, str)
        # build the block hash tree. SDMF has only one leaf.
        leaves = [hashutil.block_hash(share_data)]
        t = hashtree.HashTree(leaves)
        if list(t) != block_hash_tree:
            raise CorruptShareError(peerid, shnum, "block hash tree failure")
        share_hash_leaf = t[0]
        t2 = hashtree.IncompleteHashTree(N)
        # root_hash was checked by the signature
        t2.set_hashes({0: root_hash})
        try:
            t2.set_hashes(hashes=share_hash_chain,
                          leaves={shnum: share_hash_leaf})
        except (hashtree.BadHashError, hashtree.NotEnoughHashesError,
                IndexError), e:
            msg = "corrupt hashes: %s" % (e,)
            raise CorruptShareError(peerid, shnum, msg)
        self.log(" data valid! len=%d" % len(share_data))
        # each query comes down to this: placing validated share data into
        # self.shares
        self.shares[shnum] = share_data

    def _try_to_validate_privkey(self, enc_privkey, peerid, shnum, lp):

        alleged_privkey_s = self._node._decrypt_privkey(enc_privkey)
        alleged_writekey = hashutil.ssk_writekey_hash(alleged_privkey_s)
        if alleged_writekey != self._node.get_writekey():
            self.log("invalid privkey from %s shnum %d" %
                     (idlib.nodeid_b2a(peerid)[:8], shnum),
                     parent=lp, level=log.WEIRD, umid="YIw4tA")
            return

        # it's good
        self.log("got valid privkey from shnum %d on peerid %s" %
                 (shnum, idlib.shortnodeid_b2a(peerid)),
                 parent=lp)
        privkey = rsa.create_signing_key_from_string(alleged_privkey_s)
        self._node._populate_encprivkey(enc_privkey)
        self._node._populate_privkey(privkey)
        self._need_privkey = False

    def _query_failed(self, f, marker, peerid):
        self.log(format="query to [%(peerid)s] failed",
                 peerid=idlib.shortnodeid_b2a(peerid),
                 level=log.NOISY)
        self._status.problems[peerid] = f
        self._outstanding_queries.pop(marker, None)
        if not self._running:
            return
        self._last_failure = f
        self.remove_peer(peerid)
        level = log.WEIRD
        if f.check(DeadReferenceError):
            level = log.UNUSUAL
        self.log(format="error during query: %(f_value)s",
                 f_value=str(f.value), failure=f, level=level, umid="gOJB5g")

    def _check_for_done(self, res):
        # exit paths:
        #  return : keep waiting, no new queries
        #  return self._send_more_queries(outstanding) : send some more queries
        #  fire self._done(plaintext) : download successful
        #  raise exception : download fails

        self.log(format="_check_for_done: running=%(running)s, decoding=%(decoding)s",
                 running=self._running, decoding=self._decoding,
                 level=log.NOISY)
        if not self._running:
            return
        if self._decoding:
            return
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.verinfo

        if len(self.shares) < k:
            # we don't have enough shares yet
            return self._maybe_send_more_queries(k)
        if self._need_privkey:
            # we got k shares, but none of them had a valid privkey. TODO:
            # look further. Adding code to do this is a bit complicated, and
            # I want to avoid that complication, and this should be pretty
            # rare (k shares with bitflips in the enc_privkey but not in the
            # data blocks). If we actually do get here, the subsequent repair
            # will fail for lack of a privkey.
            self.log("got k shares but still need_privkey, bummer",
                     level=log.WEIRD, umid="MdRHPA")

        # we have enough to finish. All the shares have had their hashes
        # checked, so if something fails at this point, we don't know how
        # to fix it, so the download will fail.

        self._decoding = True # avoid reentrancy
        self._status.set_status("decoding")
        now = time.time()
        elapsed = now - self._started
        self._status.timings["fetch"] = elapsed

        d = defer.maybeDeferred(self._decode)
        d.addCallback(self._decrypt, IV, self._node._readkey)
        d.addBoth(self._done)
        return d # purely for test convenience

    def _maybe_send_more_queries(self, k):
        # we don't have enough shares yet. Should we send out more queries?
        # There are some number of queries outstanding, each for a single
        # share. If we can generate 'needed_shares' additional queries, we do
        # so. If we can't, then we know this file is a goner, and we raise
        # NotEnoughSharesError.
        self.log(format=("_maybe_send_more_queries, have=%(have)d, k=%(k)d, "
                         "outstanding=%(outstanding)d"),
                 have=len(self.shares), k=k,
                 outstanding=len(self._outstanding_queries),
                 level=log.NOISY)

        remaining_shares = k - len(self.shares)
        needed = remaining_shares - len(self._outstanding_queries)
        if not needed:
            # we have enough queries in flight already

            # TODO: but if they've been in flight for a long time, and we
            # have reason to believe that new queries might respond faster
            # (i.e. we've seen other queries come back faster, then consider
            # sending out new queries. This could help with peers which have
            # silently gone away since the servermap was updated, for which
            # we're still waiting for the 15-minute TCP disconnect to happen.
            self.log("enough queries are in flight, no more are needed",
                     level=log.NOISY)
            return

        outstanding_shnums = set([shnum
                                  for (peerid, shnum, started)
                                  in self._outstanding_queries.values()])
        # prefer low-numbered shares, they are more likely to be primary
        available_shnums = sorted(self.remaining_sharemap.keys())
        for shnum in available_shnums:
            if shnum in outstanding_shnums:
                # skip ones that are already in transit
                continue
            if shnum not in self.remaining_sharemap:
                # no servers for that shnum. note that DictOfSets removes
                # empty sets from the dict for us.
                continue
            peerid = list(self.remaining_sharemap[shnum])[0]
            # get_data will remove that peerid from the sharemap, and add the
            # query to self._outstanding_queries
            self._status.set_status("Retrieving More Shares")
            self.get_data(shnum, peerid)
            needed -= 1
            if not needed:
                break

        # at this point, we have as many outstanding queries as we can. If
        # needed!=0 then we might not have enough to recover the file.
        if needed:
            format = ("ran out of peers: "
                      "have %(have)d shares (k=%(k)d), "
                      "%(outstanding)d queries in flight, "
                      "need %(need)d more, "
                      "found %(bad)d bad shares")
            args = {"have": len(self.shares),
                    "k": k,
                    "outstanding": len(self._outstanding_queries),
                    "need": needed,
                    "bad": len(self._bad_shares),
                    }
            self.log(format=format,
                     level=log.WEIRD, umid="ezTfjw", **args)
            err = NotEnoughSharesError("%s, last failure: %s" %
                                      (format % args, self._last_failure),
                                       len(self.shares), k)
            if self._bad_shares:
                self.log("We found some bad shares this pass. You should "
                         "update the servermap and try again to check "
                         "more peers",
                         level=log.WEIRD, umid="EFkOlA")
                err.servermap = self.servermap
            raise err

        return

    def _decode(self):
        started = time.time()
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.verinfo

        # shares_dict is a dict mapping shnum to share data, but the codec
        # wants two lists.
        shareids = []; shares = []
        for shareid, share in self.shares.items():
            shareids.append(shareid)
            shares.append(share)

        assert len(shareids) >= k, len(shareids)
        # zfec really doesn't want extra shares
        shareids = shareids[:k]
        shares = shares[:k]

        fec = codec.CRSDecoder()
        fec.set_params(segsize, k, N)

        self.log("params %s, we have %d shares" % ((segsize, k, N), len(shares)))
        self.log("about to decode, shareids=%s" % (shareids,))
        d = defer.maybeDeferred(fec.decode, shares, shareids)
        def _done(buffers):
            self._status.timings["decode"] = time.time() - started
            self.log(" decode done, %d buffers" % len(buffers))
            segment = "".join(buffers)
            self.log(" joined length %d, datalength %d" %
                     (len(segment), datalength))
            segment = segment[:datalength]
            self.log(" segment len=%d" % len(segment))
            return segment
        def _err(f):
            self.log(" decode failed: %s" % f)
            return f
        d.addCallback(_done)
        d.addErrback(_err)
        return d

    def _decrypt(self, crypttext, IV, readkey):
        self._status.set_status("decrypting")
        started = time.time()
        key = hashutil.ssk_readkey_data_hash(IV, readkey)
        decryptor = AES(key)
        plaintext = decryptor.process(crypttext)
        self._status.timings["decrypt"] = time.time() - started
        return plaintext

    def _done(self, res):
        if not self._running:
            return
        self._running = False
        self._status.set_active(False)
        self._status.timings["total"] = time.time() - self._started
        # res is either the new contents, or a Failure
        if isinstance(res, failure.Failure):
            self.log("Retrieve done, with failure", failure=res,
                     level=log.UNUSUAL)
            self._status.set_status("Failed")
        else:
            self.log("Retrieve done, success!")
            self._status.set_status("Done")
            self._status.set_progress(1.0)
            # remember the encoding parameters, use them again next time
            (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
             offsets_tuple) = self.verinfo
            self._node._populate_required_shares(k)
            self._node._populate_total_shares(N)
        eventually(self._done_deferred.callback, res)

