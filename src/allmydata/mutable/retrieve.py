
import time
from itertools import count
from zope.interface import implements
from twisted.internet import defer
from twisted.python import failure
from twisted.internet.interfaces import IPushProducer, IConsumer
from foolscap.api import eventually, fireEventually, DeadReferenceError, \
     RemoteException

from allmydata.interfaces import IRetrieveStatus, NotEnoughSharesError, \
     DownloadStopped, MDMF_VERSION, SDMF_VERSION
from allmydata.util.assertutil import _assert
from allmydata.util import hashutil, log, mathutil, deferredutil
from allmydata.util.dictutil import DictOfSets
from allmydata import hashtree, codec
from allmydata.storage.server import si_b2a
from pycryptopp.cipher.aes import AES
from pycryptopp.publickey import rsa

from allmydata.mutable.common import CorruptShareError, BadShareError, \
     UncoordinatedWriteError
from allmydata.mutable.layout import MDMFSlotReadProxy

class RetrieveStatus:
    implements(IRetrieveStatus)
    statusid_counter = count(0)
    def __init__(self):
        self.timings = {}
        self.timings["fetch_per_server"] = {}
        self.timings["decode"] = 0.0
        self.timings["decrypt"] = 0.0
        self.timings["cumulative_verify"] = 0.0
        self._problems = {}
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
    def get_problems(self):
        return self._problems

    def add_fetch_timing(self, server, elapsed):
        if server not in self.timings["fetch_per_server"]:
            self.timings["fetch_per_server"][server] = []
        self.timings["fetch_per_server"][server].append(elapsed)
    def accumulate_decode_time(self, elapsed):
        self.timings["decode"] += elapsed
    def accumulate_decrypt_time(self, elapsed):
        self.timings["decrypt"] += elapsed
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
    def add_problem(self, server, f):
        serverid = server.get_serverid()
        self._problems[serverid] = f

class Marker:
    pass

class Retrieve:
    # this class is currently single-use. Eventually (in MDMF) we will make
    # it multi-use, in which case you can call download(range) multiple
    # times, and each will have a separate response chain. However the
    # Retrieve object will remain tied to a specific version of the file, and
    # will use a single ServerMap instance.
    implements(IPushProducer)

    def __init__(self, filenode, storage_broker, servermap, verinfo,
                 fetch_privkey=False, verify=False):
        self._node = filenode
        assert self._node.get_pubkey()
        self._storage_broker = storage_broker
        self._storage_index = filenode.get_storage_index()
        assert self._node.get_readkey()
        self._last_failure = None
        prefix = si_b2a(self._storage_index)[:5]
        self._log_number = log.msg("Retrieve(%s): starting" % prefix)
        self._running = True
        self._decoding = False
        self._bad_shares = set()

        self.servermap = servermap
        assert self._node.get_pubkey()
        self.verinfo = verinfo
        # during repair, we may be called upon to grab the private key, since
        # it wasn't picked up during a verify=False checker run, and we'll
        # need it for repair to generate a new version.
        self._need_privkey = verify or (fetch_privkey
                                        and not self._node.get_privkey())

        if self._need_privkey:
            # TODO: Evaluate the need for this. We'll use it if we want
            # to limit how many queries are on the wire for the privkey
            # at once.
            self._privkey_query_markers = [] # one Marker for each time we've
                                             # tried to get the privkey.

        # verify means that we are using the downloader logic to verify all
        # of our shares. This tells the downloader a few things.
        #
        # 1. We need to download all of the shares.
        # 2. We don't need to decode or decrypt the shares, since our
        #    caller doesn't care about the plaintext, only the
        #    information about which shares are or are not valid.
        # 3. When we are validating readers, we need to validate the
        #    signature on the prefix. Do we? We already do this in the
        #    servermap update?
        self._verify = verify

        self._status = RetrieveStatus()
        self._status.set_storage_index(self._storage_index)
        self._status.set_helper(False)
        self._status.set_progress(0.0)
        self._status.set_active(True)
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.verinfo
        self._status.set_size(datalength)
        self._status.set_encoding(k, N)
        self.readers = {}
        self._stopped = False
        self._pause_deferred = None
        self._offset = None
        self._read_length = None
        self.log("got seqnum %d" % self.verinfo[0])


    def get_status(self):
        return self._status

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.mutable.retrieve"
        return log.msg(*args, **kwargs)

    def _set_current_status(self, state):
        seg = "%d/%d" % (self._current_segment, self._last_segment)
        self._status.set_status("segment %s (%s)" % (seg, state))

    ###################
    # IPushProducer

    def pauseProducing(self):
        """
        I am called by my download target if we have produced too much
        data for it to handle. I make the downloader stop producing new
        data until my resumeProducing method is called.
        """
        if self._pause_deferred is not None:
            return

        # fired when the download is unpaused.
        self._old_status = self._status.get_status()
        self._set_current_status("paused")

        self._pause_deferred = defer.Deferred()


    def resumeProducing(self):
        """
        I am called by my download target once it is ready to begin
        receiving data again.
        """
        if self._pause_deferred is None:
            return

        p = self._pause_deferred
        self._pause_deferred = None
        self._status.set_status(self._old_status)

        eventually(p.callback, None)

    def stopProducing(self):
        self._stopped = True
        self.resumeProducing()


    def _check_for_paused(self, res):
        """
        I am called just before a write to the consumer. I return a
        Deferred that eventually fires with the data that is to be
        written to the consumer. If the download has not been paused,
        the Deferred fires immediately. Otherwise, the Deferred fires
        when the downloader is unpaused.
        """
        if self._pause_deferred is not None:
            d = defer.Deferred()
            self._pause_deferred.addCallback(lambda ignored: d.callback(res))
            return d
        return res

    def _check_for_stopped(self, res):
        if self._stopped:
            raise DownloadStopped("our Consumer called stopProducing()")
        return res


    def download(self, consumer=None, offset=0, size=None):
        assert IConsumer.providedBy(consumer) or self._verify

        if consumer:
            self._consumer = consumer
            # we provide IPushProducer, so streaming=True, per
            # IConsumer.
            self._consumer.registerProducer(self, streaming=True)

        self._done_deferred = defer.Deferred()
        self._offset = offset
        self._read_length = size
        self._setup_encoding_parameters()
        self._setup_download()
        self.log("starting download")
        self._started_fetching = time.time()
        # The download process beyond this is a state machine.
        # _add_active_servers will select the servers that we want to use
        # for the download, and then attempt to start downloading. After
        # each segment, it will check for doneness, reacting to broken
        # servers and corrupt shares as necessary. If it runs out of good
        # servers before downloading all of the segments, _done_deferred
        # will errback.  Otherwise, it will eventually callback with the
        # contents of the mutable file.
        self.loop()
        return self._done_deferred

    def loop(self):
        d = fireEventually(None) # avoid #237 recursion limit problem
        d.addCallback(lambda ign: self._activate_enough_servers())
        d.addCallback(lambda ign: self._download_current_segment())
        # when we're done, _download_current_segment will call _done. If we
        # aren't, it will call loop() again.
        d.addErrback(self._error)

    def _setup_download(self):
        self._started = time.time()
        self._status.set_status("Retrieving Shares")

        # how many shares do we need?
        (seqnum,
         root_hash,
         IV,
         segsize,
         datalength,
         k,
         N,
         prefix,
         offsets_tuple) = self.verinfo

        # first, which servers can we use?
        versionmap = self.servermap.make_versionmap()
        shares = versionmap[self.verinfo]
        # this sharemap is consumed as we decide to send requests
        self.remaining_sharemap = DictOfSets()
        for (shnum, server, timestamp) in shares:
            self.remaining_sharemap.add(shnum, server)
            # Reuse the SlotReader from the servermap.
            key = (self.verinfo, server.get_serverid(),
                   self._storage_index, shnum)
            if key in self.servermap.proxies:
                reader = self.servermap.proxies[key]
            else:
                reader = MDMFSlotReadProxy(server.get_rref(),
                                           self._storage_index, shnum, None)
            reader.server = server
            self.readers[shnum] = reader

        if len(self.remaining_sharemap) < k:
            self._raise_notenoughshareserror()

        self.shares = {} # maps shnum to validated blocks
        self._active_readers = [] # list of active readers for this dl.
        self._block_hash_trees = {} # shnum => hashtree

        for i in xrange(self._total_shares):
            # So we don't have to do this later.
            self._block_hash_trees[i] = hashtree.IncompleteHashTree(self._num_segments)

        # We need one share hash tree for the entire file; its leaves
        # are the roots of the block hash trees for the shares that
        # comprise it, and its root is in the verinfo.
        self.share_hash_tree = hashtree.IncompleteHashTree(N)
        self.share_hash_tree.set_hashes({0: root_hash})

    def decode(self, blocks_and_salts, segnum):
        """
        I am a helper method that the mutable file update process uses
        as a shortcut to decode and decrypt the segments that it needs
        to fetch in order to perform a file update. I take in a
        collection of blocks and salts, and pick some of those to make a
        segment with. I return the plaintext associated with that
        segment.
        """
        # We don't need the block hash trees in this case.
        self._block_hash_trees = None
        self._setup_encoding_parameters()

        # _decode_blocks() expects the output of a gatherResults that
        # contains the outputs of _validate_block() (each of which is a dict
        # mapping shnum to (block,salt) bytestrings).
        d = self._decode_blocks([blocks_and_salts], segnum)
        d.addCallback(self._decrypt_segment)
        return d


    def _setup_encoding_parameters(self):
        """
        I set up the encoding parameters, including k, n, the number
        of segments associated with this file, and the segment decoders.
        """
        (seqnum,
         root_hash,
         IV,
         segsize,
         datalength,
         k,
         n,
         known_prefix,
         offsets_tuple) = self.verinfo
        self._required_shares = k
        self._total_shares = n
        self._segment_size = segsize
        self._data_length = datalength

        if not IV:
            self._version = MDMF_VERSION
        else:
            self._version = SDMF_VERSION

        if datalength and segsize:
            self._num_segments = mathutil.div_ceil(datalength, segsize)
            self._tail_data_size = datalength % segsize
        else:
            self._num_segments = 0
            self._tail_data_size = 0

        self._segment_decoder = codec.CRSDecoder()
        self._segment_decoder.set_params(segsize, k, n)

        if  not self._tail_data_size:
            self._tail_data_size = segsize

        self._tail_segment_size = mathutil.next_multiple(self._tail_data_size,
                                                         self._required_shares)
        if self._tail_segment_size == self._segment_size:
            self._tail_decoder = self._segment_decoder
        else:
            self._tail_decoder = codec.CRSDecoder()
            self._tail_decoder.set_params(self._tail_segment_size,
                                          self._required_shares,
                                          self._total_shares)

        self.log("got encoding parameters: "
                 "k: %d "
                 "n: %d "
                 "%d segments of %d bytes each (%d byte tail segment)" % \
                 (k, n, self._num_segments, self._segment_size,
                  self._tail_segment_size))

        # Our last task is to tell the downloader where to start and
        # where to stop. We use three parameters for that:
        #   - self._start_segment: the segment that we need to start
        #     downloading from.
        #   - self._current_segment: the next segment that we need to
        #     download.
        #   - self._last_segment: The last segment that we were asked to
        #     download.
        #
        #  We say that the download is complete when
        #  self._current_segment > self._last_segment. We use
        #  self._start_segment and self._last_segment to know when to
        #  strip things off of segments, and how much to strip.
        if self._offset:
            self.log("got offset: %d" % self._offset)
            # our start segment is the first segment containing the
            # offset we were given.
            start = self._offset // self._segment_size

            _assert(start < self._num_segments,
                    start=start, num_segments=self._num_segments,
                    offset=self._offset, segment_size=self._segment_size)
            self._start_segment = start
            self.log("got start segment: %d" % self._start_segment)
        else:
            self._start_segment = 0


        # If self._read_length is None, then we want to read the whole
        # file. Otherwise, we want to read only part of the file, and
        # need to figure out where to stop reading.
        if self._read_length is not None:
            # our end segment is the last segment containing part of the
            # segment that we were asked to read.
            self.log("got read length %d" % self._read_length)
            if self._read_length != 0:
                end_data = self._offset + self._read_length

                # We don't actually need to read the byte at end_data,
                # but the one before it.
                end = (end_data - 1) // self._segment_size

                _assert(end < self._num_segments,
                        end=end, num_segments=self._num_segments,
                        end_data=end_data, offset=self._offset, read_length=self._read_length,
                        segment_size=self._segment_size)
                self._last_segment = end
            else:
                self._last_segment = self._start_segment
            self.log("got end segment: %d" % self._last_segment)
        else:
            self._last_segment = self._num_segments - 1

        self._current_segment = self._start_segment

    def _activate_enough_servers(self):
        """
        I populate self._active_readers with enough active readers to
        retrieve the contents of this mutable file. I am called before
        downloading starts, and (eventually) after each validation
        error, connection error, or other problem in the download.
        """
        # TODO: It would be cool to investigate other heuristics for
        # reader selection. For instance, the cost (in time the user
        # spends waiting for their file) of selecting a really slow server
        # that happens to have a primary share is probably more than
        # selecting a really fast server that doesn't have a primary
        # share. Maybe the servermap could be extended to provide this
        # information; it could keep track of latency information while
        # it gathers more important data, and then this routine could
        # use that to select active readers.
        #
        # (these and other questions would be easier to answer with a
        #  robust, configurable tahoe-lafs simulator, which modeled node
        #  failures, differences in node speed, and other characteristics
        #  that we expect storage servers to have.  You could have
        #  presets for really stable grids (like allmydata.com),
        #  friendnets, make it easy to configure your own settings, and
        #  then simulate the effect of big changes on these use cases
        #  instead of just reasoning about what the effect might be. Out
        #  of scope for MDMF, though.)

        # XXX: Why don't format= log messages work here?

        known_shnums = set(self.remaining_sharemap.keys())
        used_shnums = set([r.shnum for r in self._active_readers])
        unused_shnums = known_shnums - used_shnums

        if self._verify:
            new_shnums = unused_shnums # use them all
        elif len(self._active_readers) < self._required_shares:
            # need more shares
            more = self._required_shares - len(self._active_readers)
            # We favor lower numbered shares, since FEC is faster with
            # primary shares than with other shares, and lower-numbered
            # shares are more likely to be primary than higher numbered
            # shares.
            new_shnums = sorted(unused_shnums)[:more]
            if len(new_shnums) < more:
                # We don't have enough readers to retrieve the file; fail.
                self._raise_notenoughshareserror()
        else:
            new_shnums = []

        self.log("adding %d new servers to the active list" % len(new_shnums))
        for shnum in new_shnums:
            reader = self.readers[shnum]
            self._active_readers.append(reader)
            self.log("added reader for share %d" % shnum)
            # Each time we add a reader, we check to see if we need the
            # private key. If we do, we politely ask for it and then continue
            # computing. If we find that we haven't gotten it at the end of
            # segment decoding, then we'll take more drastic measures.
            if self._need_privkey and not self._node.is_readonly():
                d = reader.get_encprivkey()
                d.addCallback(self._try_to_validate_privkey, reader, reader.server)
                # XXX: don't just drop the Deferred. We need error-reporting
                # but not flow-control here.

    def _try_to_validate_prefix(self, prefix, reader):
        """
        I check that the prefix returned by a candidate server for
        retrieval matches the prefix that the servermap knows about
        (and, hence, the prefix that was validated earlier). If it does,
        I return True, which means that I approve of the use of the
        candidate server for segment retrieval. If it doesn't, I return
        False, which means that another server must be chosen.
        """
        (seqnum,
         root_hash,
         IV,
         segsize,
         datalength,
         k,
         N,
         known_prefix,
         offsets_tuple) = self.verinfo
        if known_prefix != prefix:
            self.log("prefix from share %d doesn't match" % reader.shnum)
            raise UncoordinatedWriteError("Mismatched prefix -- this could "
                                          "indicate an uncoordinated write")
        # Otherwise, we're okay -- no issues.

    def _mark_bad_share(self, server, shnum, reader, f):
        """
        I mark the given (server, shnum) as a bad share, which means that it
        will not be used anywhere else.

        There are several reasons to want to mark something as a bad
        share. These include:

            - A connection error to the server.
            - A mismatched prefix (that is, a prefix that does not match
              our local conception of the version information string).
            - A failing block hash, salt hash, share hash, or other
              integrity check.

        This method will ensure that readers that we wish to mark bad
        (for these reasons or other reasons) are not used for the rest
        of the download. Additionally, it will attempt to tell the
        remote server (with no guarantee of success) that its share is
        corrupt.
        """
        self.log("marking share %d on server %s as bad" % \
                 (shnum, server.get_name()))
        prefix = self.verinfo[-2]
        self.servermap.mark_bad_share(server, shnum, prefix)
        self._bad_shares.add((server, shnum, f))
        self._status.add_problem(server, f)
        self._last_failure = f

        # Remove the reader from _active_readers
        self._active_readers.remove(reader)
        for shnum in list(self.remaining_sharemap.keys()):
            self.remaining_sharemap.discard(shnum, reader.server)

        if f.check(BadShareError):
            self.notify_server_corruption(server, shnum, str(f.value))

    def _download_current_segment(self):
        """
        I download, validate, decode, decrypt, and assemble the segment
        that this Retrieve is currently responsible for downloading.
        """
        if self._current_segment > self._last_segment:
            # No more segments to download, we're done.
            self.log("got plaintext, done")
            return self._done()
        elif self._verify and len(self._active_readers) == 0:
            self.log("no more good shares, no need to keep verifying")
            return self._done()
        self.log("on segment %d of %d" %
                 (self._current_segment + 1, self._num_segments))
        d = self._process_segment(self._current_segment)
        d.addCallback(lambda ign: self.loop())
        return d

    def _process_segment(self, segnum):
        """
        I download, validate, decode, and decrypt one segment of the
        file that this Retrieve is retrieving. This means coordinating
        the process of getting k blocks of that file, validating them,
        assembling them into one segment with the decoder, and then
        decrypting them.
        """
        self.log("processing segment %d" % segnum)

        # TODO: The old code uses a marker. Should this code do that
        # too? What did the Marker do?

        # We need to ask each of our active readers for its block and
        # salt. We will then validate those. If validation is
        # successful, we will assemble the results into plaintext.
        ds = []
        for reader in self._active_readers:
            started = time.time()
            d1 = reader.get_block_and_salt(segnum)
            d2,d3 = self._get_needed_hashes(reader, segnum)
            d = deferredutil.gatherResults([d1,d2,d3])
            d.addCallback(self._validate_block, segnum, reader, reader.server, started)
            # _handle_bad_share takes care of recoverable errors (by dropping
            # that share and returning None). Any other errors (i.e. code
            # bugs) are passed through and cause the retrieve to fail.
            d.addErrback(self._handle_bad_share, [reader])
            ds.append(d)
        dl = deferredutil.gatherResults(ds)
        if self._verify:
            dl.addCallback(lambda ignored: "")
            dl.addCallback(self._set_segment)
        else:
            dl.addCallback(self._maybe_decode_and_decrypt_segment, segnum)
        return dl


    def _maybe_decode_and_decrypt_segment(self, results, segnum):
        """
        I take the results of fetching and validating the blocks from
        _process_segment. If validation and fetching succeeded without
        incident, I will proceed with decoding and decryption. Otherwise, I
        will do nothing.
        """
        self.log("trying to decode and decrypt segment %d" % segnum)

        # 'results' is the output of a gatherResults set up in
        # _process_segment(). Each component Deferred will either contain the
        # non-Failure output of _validate_block() for a single block (i.e.
        # {segnum:(block,salt)}), or None if _validate_block threw an
        # exception and _validation_or_decoding_failed handled it (by
        # dropping that server).

        if None in results:
            self.log("some validation operations failed; not proceeding")
            return defer.succeed(None)
        self.log("everything looks ok, building segment %d" % segnum)
        d = self._decode_blocks(results, segnum)
        d.addCallback(self._decrypt_segment)
        # check to see whether we've been paused before writing
        # anything.
        d.addCallback(self._check_for_paused)
        d.addCallback(self._check_for_stopped)
        d.addCallback(self._set_segment)
        return d


    def _set_segment(self, segment):
        """
        Given a plaintext segment, I register that segment with the
        target that is handling the file download.
        """
        self.log("got plaintext for segment %d" % self._current_segment)
        if self._current_segment == self._start_segment:
            # We're on the first segment. It's possible that we want
            # only some part of the end of this segment, and that we
            # just downloaded the whole thing to get that part. If so,
            # we need to account for that and give the reader just the
            # data that they want.
            n = self._offset % self._segment_size
            self.log("stripping %d bytes off of the first segment" % n)
            self.log("original segment length: %d" % len(segment))
            segment = segment[n:]
            self.log("new segment length: %d" % len(segment))

        if self._current_segment == self._last_segment and self._read_length is not None:
            # We're on the last segment. It's possible that we only want
            # part of the beginning of this segment, and that we
            # downloaded the whole thing anyway. Make sure to give the
            # caller only the portion of the segment that they want to
            # receive.
            extra = self._read_length
            if self._start_segment != self._last_segment:
                extra -= self._segment_size - \
                            (self._offset % self._segment_size)
            extra %= self._segment_size
            self.log("original segment length: %d" % len(segment))
            segment = segment[:extra]
            self.log("new segment length: %d" % len(segment))
            self.log("only taking %d bytes of the last segment" % extra)

        if not self._verify:
            self._consumer.write(segment)
        else:
            # we don't care about the plaintext if we are doing a verify.
            segment = None
        self._current_segment += 1


    def _handle_bad_share(self, f, readers):
        """
        I am called when a block or a salt fails to correctly validate, or when
        the decryption or decoding operation fails for some reason.  I react to
        this failure by notifying the remote server of corruption, and then
        removing the remote server from further activity.
        """
        # these are the errors we can tolerate: by giving up on this share
        # and finding others to replace it. Any other errors (i.e. coding
        # bugs) are re-raised, causing the download to fail.
        f.trap(DeadReferenceError, RemoteException, BadShareError)

        # DeadReferenceError happens when we try to fetch data from a server
        # that has gone away. RemoteException happens if the server had an
        # internal error. BadShareError encompasses: (UnknownVersionError,
        # LayoutInvalid, struct.error) which happen when we get obviously
        # wrong data, and CorruptShareError which happens later, when we
        # perform integrity checks on the data.

        assert isinstance(readers, list)
        bad_shnums = [reader.shnum for reader in readers]

        self.log("validation or decoding failed on share(s) %s, server(s) %s "
                 ", segment %d: %s" % \
                 (bad_shnums, readers, self._current_segment, str(f)))
        for reader in readers:
            self._mark_bad_share(reader.server, reader.shnum, reader, f)
        return None


    def _validate_block(self, results, segnum, reader, server, started):
        """
        I validate a block from one share on a remote server.
        """
        # Grab the part of the block hash tree that is necessary to
        # validate this block, then generate the block hash root.
        self.log("validating share %d for segment %d" % (reader.shnum,
                                                             segnum))
        elapsed = time.time() - started
        self._status.add_fetch_timing(server, elapsed)
        self._set_current_status("validating blocks")

        block_and_salt, blockhashes, sharehashes = results
        block, salt = block_and_salt
        assert type(block) is str, (block, salt)

        blockhashes = dict(enumerate(blockhashes))
        self.log("the reader gave me the following blockhashes: %s" % \
                 blockhashes.keys())
        self.log("the reader gave me the following sharehashes: %s" % \
                 sharehashes.keys())
        bht = self._block_hash_trees[reader.shnum]

        if bht.needed_hashes(segnum, include_leaf=True):
            try:
                bht.set_hashes(blockhashes)
            except (hashtree.BadHashError, hashtree.NotEnoughHashesError, \
                    IndexError), e:
                raise CorruptShareError(server,
                                        reader.shnum,
                                        "block hash tree failure: %s" % e)

        if self._version == MDMF_VERSION:
            blockhash = hashutil.block_hash(salt + block)
        else:
            blockhash = hashutil.block_hash(block)
        # If this works without an error, then validation is
        # successful.
        try:
           bht.set_hashes(leaves={segnum: blockhash})
        except (hashtree.BadHashError, hashtree.NotEnoughHashesError, \
                IndexError), e:
            raise CorruptShareError(server,
                                    reader.shnum,
                                    "block hash tree failure: %s" % e)

        # Reaching this point means that we know that this segment
        # is correct. Now we need to check to see whether the share
        # hash chain is also correct.
        # SDMF wrote share hash chains that didn't contain the
        # leaves, which would be produced from the block hash tree.
        # So we need to validate the block hash tree first. If
        # successful, then bht[0] will contain the root for the
        # shnum, which will be a leaf in the share hash tree, which
        # will allow us to validate the rest of the tree.
        try:
            self.share_hash_tree.set_hashes(hashes=sharehashes,
                                        leaves={reader.shnum: bht[0]})
        except (hashtree.BadHashError, hashtree.NotEnoughHashesError, \
                IndexError), e:
            raise CorruptShareError(server,
                                    reader.shnum,
                                    "corrupt hashes: %s" % e)

        self.log('share %d is valid for segment %d' % (reader.shnum,
                                                       segnum))
        return {reader.shnum: (block, salt)}


    def _get_needed_hashes(self, reader, segnum):
        """
        I get the hashes needed to validate segnum from the reader, then return
        to my caller when this is done.
        """
        bht = self._block_hash_trees[reader.shnum]
        needed = bht.needed_hashes(segnum, include_leaf=True)
        # The root of the block hash tree is also a leaf in the share
        # hash tree. So we don't need to fetch it from the remote
        # server. In the case of files with one segment, this means that
        # we won't fetch any block hash tree from the remote server,
        # since the hash of each share of the file is the entire block
        # hash tree, and is a leaf in the share hash tree. This is fine,
        # since any share corruption will be detected in the share hash
        # tree.
        #needed.discard(0)
        self.log("getting blockhashes for segment %d, share %d: %s" % \
                 (segnum, reader.shnum, str(needed)))
        # TODO is force_remote necessary here?
        d1 = reader.get_blockhashes(needed, force_remote=False)
        if self.share_hash_tree.needed_hashes(reader.shnum):
            need = self.share_hash_tree.needed_hashes(reader.shnum)
            self.log("also need sharehashes for share %d: %s" % (reader.shnum,
                                                                 str(need)))
            d2 = reader.get_sharehashes(need, force_remote=False)
        else:
            d2 = defer.succeed({}) # the logic in the next method
                                   # expects a dict
        return d1,d2


    def _decode_blocks(self, results, segnum):
        """
        I take a list of k blocks and salts, and decode that into a
        single encrypted segment.
        """
        # 'results' is one or more dicts (each {shnum:(block,salt)}), and we
        # want to merge them all
        blocks_and_salts = {}
        for d in results:
            blocks_and_salts.update(d)

        # All of these blocks should have the same salt; in SDMF, it is
        # the file-wide IV, while in MDMF it is the per-segment salt. In
        # either case, we just need to get one of them and use it.
        #
        # d.items()[0] is like (shnum, (block, salt))
        # d.items()[0][1] is like (block, salt)
        # d.items()[0][1][1] is the salt.
        salt = blocks_and_salts.items()[0][1][1]
        # Next, extract just the blocks from the dict. We'll use the
        # salt in the next step.
        share_and_shareids = [(k, v[0]) for k, v in blocks_and_salts.items()]
        d2 = dict(share_and_shareids)
        shareids = []
        shares = []
        for shareid, share in d2.items():
            shareids.append(shareid)
            shares.append(share)

        self._set_current_status("decoding")
        started = time.time()
        assert len(shareids) >= self._required_shares, len(shareids)
        # zfec really doesn't want extra shares
        shareids = shareids[:self._required_shares]
        shares = shares[:self._required_shares]
        self.log("decoding segment %d" % segnum)
        if segnum == self._num_segments - 1:
            d = defer.maybeDeferred(self._tail_decoder.decode, shares, shareids)
        else:
            d = defer.maybeDeferred(self._segment_decoder.decode, shares, shareids)
        def _process(buffers):
            segment = "".join(buffers)
            self.log(format="now decoding segment %(segnum)s of %(numsegs)s",
                     segnum=segnum,
                     numsegs=self._num_segments,
                     level=log.NOISY)
            self.log(" joined length %d, datalength %d" %
                     (len(segment), self._data_length))
            if segnum == self._num_segments - 1:
                size_to_use = self._tail_data_size
            else:
                size_to_use = self._segment_size
            segment = segment[:size_to_use]
            self.log(" segment len=%d" % len(segment))
            self._status.accumulate_decode_time(time.time() - started)
            return segment, salt
        d.addCallback(_process)
        return d


    def _decrypt_segment(self, segment_and_salt):
        """
        I take a single segment and its salt, and decrypt it. I return
        the plaintext of the segment that is in my argument.
        """
        segment, salt = segment_and_salt
        self._set_current_status("decrypting")
        self.log("decrypting segment %d" % self._current_segment)
        started = time.time()
        key = hashutil.ssk_readkey_data_hash(salt, self._node.get_readkey())
        decryptor = AES(key)
        plaintext = decryptor.process(segment)
        self._status.accumulate_decrypt_time(time.time() - started)
        return plaintext


    def notify_server_corruption(self, server, shnum, reason):
        rref = server.get_rref()
        rref.callRemoteOnly("advise_corrupt_share",
                            "mutable", self._storage_index, shnum, reason)


    def _try_to_validate_privkey(self, enc_privkey, reader, server):
        alleged_privkey_s = self._node._decrypt_privkey(enc_privkey)
        alleged_writekey = hashutil.ssk_writekey_hash(alleged_privkey_s)
        if alleged_writekey != self._node.get_writekey():
            self.log("invalid privkey from %s shnum %d" %
                     (reader, reader.shnum),
                     level=log.WEIRD, umid="YIw4tA")
            if self._verify:
                self.servermap.mark_bad_share(server, reader.shnum,
                                              self.verinfo[-2])
                e = CorruptShareError(server,
                                      reader.shnum,
                                      "invalid privkey")
                f = failure.Failure(e)
                self._bad_shares.add((server, reader.shnum, f))
            return

        # it's good
        self.log("got valid privkey from shnum %d on reader %s" %
                 (reader.shnum, reader))
        privkey = rsa.create_signing_key_from_string(alleged_privkey_s)
        self._node._populate_encprivkey(enc_privkey)
        self._node._populate_privkey(privkey)
        self._need_privkey = False



    def _done(self):
        """
        I am called by _download_current_segment when the download process
        has finished successfully. After making some useful logging
        statements, I return the decrypted contents to the owner of this
        Retrieve object through self._done_deferred.
        """
        self._running = False
        self._status.set_active(False)
        now = time.time()
        self._status.timings['total'] = now - self._started
        self._status.timings['fetch'] = now - self._started_fetching
        self._status.set_status("Finished")
        self._status.set_progress(1.0)

        # remember the encoding parameters, use them again next time
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.verinfo
        self._node._populate_required_shares(k)
        self._node._populate_total_shares(N)

        if self._verify:
            ret = self._bad_shares
            self.log("done verifying, found %d bad shares" % len(ret))
        else:
            # TODO: upload status here?
            ret = self._consumer
            self._consumer.unregisterProducer()
        eventually(self._done_deferred.callback, ret)


    def _raise_notenoughshareserror(self):
        """
        I am called when there are not enough active servers left to complete
        the download. After making some useful logging statements, I throw an
        exception to that effect to the caller of this Retrieve object through
        self._done_deferred.
        """

        format = ("ran out of servers: "
                  "have %(have)d of %(total)d segments; "
                  "found %(bad)d bad shares; "
                  "have %(remaining)d remaining shares of the right version; "
                  "encoding %(k)d-of-%(n)d")
        args = {"have": self._current_segment,
                "total": self._num_segments,
                "need": self._last_segment,
                "k": self._required_shares,
                "n": self._total_shares,
                "bad": len(self._bad_shares),
                "remaining": len(self.remaining_sharemap),
               }
        raise NotEnoughSharesError("%s, last failure: %s" %
                                   (format % args, str(self._last_failure)))

    def _error(self, f):
        # all errors, including NotEnoughSharesError, land here
        self._running = False
        self._status.set_active(False)
        now = time.time()
        self._status.timings['total'] = now - self._started
        self._status.timings['fetch'] = now - self._started_fetching
        self._status.set_status("Failed")
        eventually(self._done_deferred.errback, f)
