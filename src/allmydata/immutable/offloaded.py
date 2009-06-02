
import os, stat, time, weakref
from zope.interface import implements
from twisted.application import service
from twisted.internet import defer
from foolscap.api import Referenceable, DeadReferenceError, eventually
import allmydata # for __full_version__
from allmydata import interfaces, uri
from allmydata.storage.server import si_b2a
from allmydata.immutable import upload
from allmydata.immutable.layout import ReadBucketProxy
from allmydata.util.assertutil import precondition
from allmydata.util import idlib, log, observer, fileutil, hashutil, dictutil


class NotEnoughWritersError(Exception):
    pass


class CHKCheckerAndUEBFetcher:
    """I check to see if a file is already present in the grid. I also fetch
    the URI Extension Block, which is useful for an uploading client who
    wants to avoid the work of encryption and encoding.

    I return False if the file is not completely healthy: i.e. if there are
    less than 'N' shares present.

    If the file is completely healthy, I return a tuple of (sharemap,
    UEB_data, UEB_hash).
    """

    def __init__(self, peer_getter, storage_index, logparent=None):
        self._peer_getter = peer_getter
        self._found_shares = set()
        self._storage_index = storage_index
        self._sharemap = dictutil.DictOfSets()
        self._readers = set()
        self._ueb_hash = None
        self._ueb_data = None
        self._logparent = logparent

    def log(self, *args, **kwargs):
        if 'facility' not in kwargs:
            kwargs['facility'] = "tahoe.helper.chk.checkandUEBfetch"
        if 'parent' not in kwargs:
            kwargs['parent'] = self._logparent
        return log.msg(*args, **kwargs)

    def check(self):
        d = self._get_all_shareholders(self._storage_index)
        d.addCallback(self._get_uri_extension)
        d.addCallback(self._done)
        return d

    def _get_all_shareholders(self, storage_index):
        dl = []
        for (peerid, ss) in self._peer_getter(storage_index):
            d = ss.callRemote("get_buckets", storage_index)
            d.addCallbacks(self._got_response, self._got_error,
                           callbackArgs=(peerid,))
            dl.append(d)
        return defer.DeferredList(dl)

    def _got_response(self, buckets, peerid):
        # buckets is a dict: maps shum to an rref of the server who holds it
        shnums_s = ",".join([str(shnum) for shnum in buckets])
        self.log("got_response: [%s] has %d shares (%s)" %
                 (idlib.shortnodeid_b2a(peerid), len(buckets), shnums_s),
                 level=log.NOISY)
        self._found_shares.update(buckets.keys())
        for k in buckets:
            self._sharemap.add(k, peerid)
        self._readers.update( [ (bucket, peerid)
                                for bucket in buckets.values() ] )

    def _got_error(self, f):
        if f.check(DeadReferenceError):
            return
        log.err(f, parent=self._logparent)
        pass

    def _get_uri_extension(self, res):
        # assume that we can pull the UEB from any share. If we get an error,
        # declare the whole file unavailable.
        if not self._readers:
            self.log("no readers, so no UEB", level=log.NOISY)
            return
        b,peerid = self._readers.pop()
        rbp = ReadBucketProxy(b, peerid, si_b2a(self._storage_index))
        d = rbp.get_uri_extension()
        d.addCallback(self._got_uri_extension)
        d.addErrback(self._ueb_error)
        return d

    def _got_uri_extension(self, ueb):
        self.log("_got_uri_extension", level=log.NOISY)
        self._ueb_hash = hashutil.uri_extension_hash(ueb)
        self._ueb_data = uri.unpack_extension(ueb)

    def _ueb_error(self, f):
        # an error means the file is unavailable, but the overall check
        # shouldn't fail.
        self.log("UEB fetch failed", failure=f, level=log.WEIRD, umid="sJLKVg")
        return None

    def _done(self, res):
        if self._ueb_data:
            found = len(self._found_shares)
            total = self._ueb_data['total_shares']
            self.log(format="got %(found)d shares of %(total)d",
                     found=found, total=total, level=log.NOISY)
            if found < total:
                # not all shares are present in the grid
                self.log("not enough to qualify, file not found in grid",
                         level=log.NOISY)
                return False
            # all shares are present
            self.log("all shares present, file is found in grid",
                     level=log.NOISY)
            return (self._sharemap, self._ueb_data, self._ueb_hash)
        # no shares are present
        self.log("unable to find UEB data, file not found in grid",
                 level=log.NOISY)
        return False


class CHKUploadHelper(Referenceable, upload.CHKUploader):
    """I am the helper-server -side counterpart to AssistedUploader. I handle
    peer selection, encoding, and share pushing. I read ciphertext from the
    remote AssistedUploader.
    """
    implements(interfaces.RICHKUploadHelper)
    VERSION = { "http://allmydata.org/tahoe/protocols/helper/chk-upload/v1" :
                 { },
                "application-version": str(allmydata.__full_version__),
                }

    def __init__(self, storage_index, helper,
                 incoming_file, encoding_file,
                 results, log_number):
        self._storage_index = storage_index
        self._helper = helper
        self._incoming_file = incoming_file
        self._encoding_file = encoding_file
        self._upload_id = si_b2a(storage_index)[:5]
        self._log_number = log_number
        self._results = results
        self._upload_status = upload.UploadStatus()
        self._upload_status.set_helper(False)
        self._upload_status.set_storage_index(storage_index)
        self._upload_status.set_status("fetching ciphertext")
        self._upload_status.set_progress(0, 1.0)
        self._helper.log("CHKUploadHelper starting for SI %s" % self._upload_id,
                         parent=log_number)

        self._client = helper.parent
        self._fetcher = CHKCiphertextFetcher(self, incoming_file, encoding_file,
                                             self._log_number)
        self._reader = LocalCiphertextReader(self, storage_index, encoding_file)
        self._finished_observers = observer.OneShotObserverList()

        d = self._fetcher.when_done()
        d.addCallback(lambda res: self._reader.start())
        d.addCallback(lambda res: self.start_encrypted(self._reader))
        d.addCallback(self._finished)
        d.addErrback(self._failed)

    def log(self, *args, **kwargs):
        if 'facility' not in kwargs:
            kwargs['facility'] = "tahoe.helper.chk"
        return upload.CHKUploader.log(self, *args, **kwargs)

    def start(self):
        self._started = time.time()
        # determine if we need to upload the file. If so, return ({},self) .
        # If not, return (UploadResults,None) .
        self.log("deciding whether to upload the file or not", level=log.NOISY)
        if os.path.exists(self._encoding_file):
            # we have the whole file, and we might be encoding it (or the
            # encode/upload might have failed, and we need to restart it).
            self.log("ciphertext already in place", level=log.UNUSUAL)
            return (self._results, self)
        if os.path.exists(self._incoming_file):
            # we have some of the file, but not all of it (otherwise we'd be
            # encoding). The caller might be useful.
            self.log("partial ciphertext already present", level=log.UNUSUAL)
            return (self._results, self)
        # we don't remember uploading this file
        self.log("no ciphertext yet", level=log.NOISY)
        return (self._results, self)

    def remote_get_version(self):
        return self.VERSION

    def remote_upload(self, reader):
        # reader is an RIEncryptedUploadable. I am specified to return an
        # UploadResults dictionary.

        # let our fetcher pull ciphertext from the reader.
        self._fetcher.add_reader(reader)
        # and also hashes
        self._reader.add_reader(reader)

        # and inform the client when the upload has finished
        return self._finished_observers.when_fired()

    def _finished(self, uploadresults):
        precondition(isinstance(uploadresults.verifycapstr, str), uploadresults.verifycapstr)
        assert interfaces.IUploadResults.providedBy(uploadresults), uploadresults
        r = uploadresults
        v = uri.from_string(r.verifycapstr)
        r.uri_extension_hash = v.uri_extension_hash
        f_times = self._fetcher.get_times()
        r.timings["cumulative_fetch"] = f_times["cumulative_fetch"]
        r.ciphertext_fetched = self._fetcher.get_ciphertext_fetched()
        r.timings["total_fetch"] = f_times["total"]
        self._reader.close()
        os.unlink(self._encoding_file)
        self._finished_observers.fire(r)
        self._helper.upload_finished(self._storage_index, v.size)
        del self._reader

    def _failed(self, f):
        self.log(format="CHKUploadHelper(%(si)s) failed",
                 si=si_b2a(self._storage_index)[:5],
                 failure=f,
                 level=log.UNUSUAL)
        self._finished_observers.fire(f)
        self._helper.upload_finished(self._storage_index, 0)
        del self._reader

class AskUntilSuccessMixin:
    # create me with a _reader array
    _last_failure = None

    def add_reader(self, reader):
        self._readers.append(reader)

    def call(self, *args, **kwargs):
        if not self._readers:
            raise NotEnoughWritersError("ran out of assisted uploaders, last failure was %s" % self._last_failure)
        rr = self._readers[0]
        d = rr.callRemote(*args, **kwargs)
        def _err(f):
            self._last_failure = f
            if rr in self._readers:
                self._readers.remove(rr)
            self._upload_helper.log("call to assisted uploader %s failed" % rr,
                                    failure=f, level=log.UNUSUAL)
            # we can try again with someone else who's left
            return self.call(*args, **kwargs)
        d.addErrback(_err)
        return d

class CHKCiphertextFetcher(AskUntilSuccessMixin):
    """I use one or more remote RIEncryptedUploadable instances to gather
    ciphertext on disk. When I'm done, the file I create can be used by a
    LocalCiphertextReader to satisfy the ciphertext needs of a CHK upload
    process.

    I begin pulling ciphertext as soon as a reader is added. I remove readers
    when they have any sort of error. If the last reader is removed, I fire
    my when_done() Deferred with a failure.

    I fire my when_done() Deferred (with None) immediately after I have moved
    the ciphertext to 'encoded_file'.
    """

    def __init__(self, helper, incoming_file, encoded_file, logparent):
        self._upload_helper = helper
        self._incoming_file = incoming_file
        self._encoding_file = encoded_file
        self._upload_id = helper._upload_id
        self._log_parent = logparent
        self._done_observers = observer.OneShotObserverList()
        self._readers = []
        self._started = False
        self._f = None
        self._times = {
            "cumulative_fetch": 0.0,
            "total": 0.0,
            }
        self._ciphertext_fetched = 0

    def log(self, *args, **kwargs):
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.helper.chkupload.fetch"
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_parent
        return log.msg(*args, **kwargs)

    def add_reader(self, reader):
        AskUntilSuccessMixin.add_reader(self, reader)
        eventually(self._start)

    def _start(self):
        if self._started:
            return
        self._started = True
        started = time.time()

        if os.path.exists(self._encoding_file):
            self.log("ciphertext already present, bypassing fetch",
                     level=log.UNUSUAL)
            # we'll still need the plaintext hashes (when
            # LocalCiphertextReader.get_plaintext_hashtree_leaves() is
            # called), and currently the easiest way to get them is to ask
            # the sender for the last byte of ciphertext. That will provoke
            # them into reading and hashing (but not sending) everything
            # else.
            have = os.stat(self._encoding_file)[stat.ST_SIZE]
            d = self.call("read_encrypted", have-1, 1)
            d.addCallback(self._done2, started)
            return

        # first, find out how large the file is going to be
        d = self.call("get_size")
        d.addCallback(self._got_size)
        d.addCallback(self._start_reading)
        d.addCallback(self._done)
        d.addCallback(self._done2, started)
        d.addErrback(self._failed)

    def _got_size(self, size):
        self.log("total size is %d bytes" % size, level=log.NOISY)
        self._upload_helper._upload_status.set_size(size)
        self._expected_size = size

    def _start_reading(self, res):
        # then find out how much crypttext we have on disk
        if os.path.exists(self._incoming_file):
            self._have = os.stat(self._incoming_file)[stat.ST_SIZE]
            self._upload_helper._helper.count("chk_upload_helper.resumes")
            self.log("we already have %d bytes" % self._have, level=log.NOISY)
        else:
            self._have = 0
            self.log("we do not have any ciphertext yet", level=log.NOISY)
        self.log("starting ciphertext fetch", level=log.NOISY)
        self._f = open(self._incoming_file, "ab")

        # now loop to pull the data from the readers
        d = defer.Deferred()
        self._loop(d)
        # this Deferred will be fired once the last byte has been written to
        # self._f
        return d

    # read data in 50kB chunks. We should choose a more considered number
    # here, possibly letting the client specify it. The goal should be to
    # keep the RTT*bandwidth to be less than 10% of the chunk size, to reduce
    # the upload bandwidth lost because this protocol is non-windowing. Too
    # large, however, means more memory consumption for both ends. Something
    # that can be transferred in, say, 10 seconds sounds about right. On my
    # home DSL line (50kBps upstream), that suggests 500kB. Most lines are
    # slower, maybe 10kBps, which suggests 100kB, and that's a bit more
    # memory than I want to hang on to, so I'm going to go with 50kB and see
    # how that works.
    CHUNK_SIZE = 50*1024

    def _loop(self, fire_when_done):
        # this slightly weird structure is needed because Deferreds don't do
        # tail-recursion, so it is important to let each one retire promptly.
        # Simply chaining them will cause a stack overflow at the end of a
        # transfer that involves more than a few hundred chunks.
        # 'fire_when_done' lives a long time, but the Deferreds returned by
        # the inner _fetch() call do not.
        start = time.time()
        d = defer.maybeDeferred(self._fetch)
        def _done(finished):
            elapsed = time.time() - start
            self._times["cumulative_fetch"] += elapsed
            if finished:
                self.log("finished reading ciphertext", level=log.NOISY)
                fire_when_done.callback(None)
            else:
                self._loop(fire_when_done)
        def _err(f):
            self.log(format="[%(si)s] ciphertext read failed",
                     si=self._upload_id, failure=f, level=log.UNUSUAL)
            fire_when_done.errback(f)
        d.addCallbacks(_done, _err)
        return None

    def _fetch(self):
        needed = self._expected_size - self._have
        fetch_size = min(needed, self.CHUNK_SIZE)
        if fetch_size == 0:
            self._upload_helper._upload_status.set_progress(1, 1.0)
            return True # all done
        percent = 0.0
        if self._expected_size:
            percent = 1.0 * (self._have+fetch_size) / self._expected_size
        self.log(format="fetching [%(si)s] %(start)d-%(end)d of %(total)d (%(percent)d%%)",
                 si=self._upload_id,
                 start=self._have,
                 end=self._have+fetch_size,
                 total=self._expected_size,
                 percent=int(100.0*percent),
                 level=log.NOISY)
        d = self.call("read_encrypted", self._have, fetch_size)
        def _got_data(ciphertext_v):
            for data in ciphertext_v:
                self._f.write(data)
                self._have += len(data)
                self._ciphertext_fetched += len(data)
                self._upload_helper._helper.count("chk_upload_helper.fetched_bytes", len(data))
                self._upload_helper._upload_status.set_progress(1, percent)
            return False # not done
        d.addCallback(_got_data)
        return d

    def _done(self, res):
        self._f.close()
        self._f = None
        self.log(format="done fetching ciphertext, size=%(size)d",
                 size=os.stat(self._incoming_file)[stat.ST_SIZE],
                 level=log.NOISY)
        os.rename(self._incoming_file, self._encoding_file)

    def _done2(self, _ignored, started):
        self.log("done2", level=log.NOISY)
        elapsed = time.time() - started
        self._times["total"] = elapsed
        self._readers = []
        self._done_observers.fire(None)

    def _failed(self, f):
        if self._f:
            self._f.close()
        self._readers = []
        self._done_observers.fire(f)

    def when_done(self):
        return self._done_observers.when_fired()

    def get_times(self):
        return self._times

    def get_ciphertext_fetched(self):
        return self._ciphertext_fetched


class LocalCiphertextReader(AskUntilSuccessMixin):
    implements(interfaces.IEncryptedUploadable)

    def __init__(self, upload_helper, storage_index, encoding_file):
        self._readers = []
        self._upload_helper = upload_helper
        self._storage_index = storage_index
        self._encoding_file = encoding_file
        self._status = None

    def start(self):
        self._upload_helper._upload_status.set_status("pushing")
        self._size = os.stat(self._encoding_file)[stat.ST_SIZE]
        self.f = open(self._encoding_file, "rb")

    def get_size(self):
        return defer.succeed(self._size)

    def get_all_encoding_parameters(self):
        return self.call("get_all_encoding_parameters")

    def get_storage_index(self):
        return defer.succeed(self._storage_index)

    def read_encrypted(self, length, hash_only):
        assert hash_only is False
        d = defer.maybeDeferred(self.f.read, length)
        d.addCallback(lambda data: [data])
        return d

    def close(self):
        self.f.close()
        # ??. I'm not sure if it makes sense to forward the close message.
        return self.call("close")



class Helper(Referenceable, service.MultiService):
    implements(interfaces.RIHelper, interfaces.IStatsProducer)
    # this is the non-distributed version. When we need to have multiple
    # helpers, this object will become the HelperCoordinator, and will query
    # the farm of Helpers to see if anyone has the storage_index of interest,
    # and send the request off to them. If nobody has it, we'll choose a
    # helper at random.

    name = "helper"
    VERSION = { "http://allmydata.org/tahoe/protocols/helper/v1" :
                 { },
                "application-version": str(allmydata.__full_version__),
                }
    chk_upload_helper_class = CHKUploadHelper
    MAX_UPLOAD_STATUSES = 10

    def __init__(self, basedir, stats_provider=None):
        self._basedir = basedir
        self._chk_incoming = os.path.join(basedir, "CHK_incoming")
        self._chk_encoding = os.path.join(basedir, "CHK_encoding")
        fileutil.make_dirs(self._chk_incoming)
        fileutil.make_dirs(self._chk_encoding)
        self._active_uploads = {}
        self._all_uploads = weakref.WeakKeyDictionary() # for debugging
        self._all_upload_statuses = weakref.WeakKeyDictionary()
        self._recent_upload_statuses = []
        self.stats_provider = stats_provider
        if stats_provider:
            stats_provider.register_producer(self)
        self._counters = {"chk_upload_helper.upload_requests": 0,
                          "chk_upload_helper.upload_already_present": 0,
                          "chk_upload_helper.upload_need_upload": 0,
                          "chk_upload_helper.resumes": 0,
                          "chk_upload_helper.fetched_bytes": 0,
                          "chk_upload_helper.encoded_bytes": 0,
                          }
        service.MultiService.__init__(self)

    def setServiceParent(self, parent):
        service.MultiService.setServiceParent(self, parent)

    def log(self, *args, **kwargs):
        if 'facility' not in kwargs:
            kwargs['facility'] = "tahoe.helper"
        return self.parent.log(*args, **kwargs)

    def count(self, key, value=1):
        if self.stats_provider:
            self.stats_provider.count(key, value)
        self._counters[key] += value

    def get_stats(self):
        OLD = 86400*2 # 48hours
        now = time.time()
        inc_count = inc_size = inc_size_old = 0
        enc_count = enc_size = enc_size_old = 0
        inc = os.listdir(self._chk_incoming)
        enc = os.listdir(self._chk_encoding)
        for f in inc:
            s = os.stat(os.path.join(self._chk_incoming, f))
            size = s[stat.ST_SIZE]
            mtime = s[stat.ST_MTIME]
            inc_count += 1
            inc_size += size
            if now - mtime > OLD:
                inc_size_old += size
        for f in enc:
            s = os.stat(os.path.join(self._chk_encoding, f))
            size = s[stat.ST_SIZE]
            mtime = s[stat.ST_MTIME]
            enc_count += 1
            enc_size += size
            if now - mtime > OLD:
                enc_size_old += size
        stats = { 'chk_upload_helper.active_uploads': len(self._active_uploads),
                  'chk_upload_helper.incoming_count': inc_count,
                  'chk_upload_helper.incoming_size': inc_size,
                  'chk_upload_helper.incoming_size_old': inc_size_old,
                  'chk_upload_helper.encoding_count': enc_count,
                  'chk_upload_helper.encoding_size': enc_size,
                  'chk_upload_helper.encoding_size_old': enc_size_old,
                  }
        stats.update(self._counters)
        return stats

    def remote_get_version(self):
        return self.VERSION

    def remote_upload_chk(self, storage_index):
        self.count("chk_upload_helper.upload_requests")
        r = upload.UploadResults()
        started = time.time()
        si_s = si_b2a(storage_index)
        lp = self.log(format="helper: upload_chk query for SI %(si)s", si=si_s)
        incoming_file = os.path.join(self._chk_incoming, si_s)
        encoding_file = os.path.join(self._chk_encoding, si_s)
        if storage_index in self._active_uploads:
            self.log("upload is currently active", parent=lp)
            uh = self._active_uploads[storage_index]
            return uh.start()

        d = self._check_for_chk_already_in_grid(storage_index, r, lp)
        def _checked(already_present):
            elapsed = time.time() - started
            r.timings['existence_check'] = elapsed
            if already_present:
                # the necessary results are placed in the UploadResults
                self.count("chk_upload_helper.upload_already_present")
                self.log("file already found in grid", parent=lp)
                return (r, None)

            self.count("chk_upload_helper.upload_need_upload")
            # the file is not present in the grid, by which we mean there are
            # less than 'N' shares available.
            self.log("unable to find file in the grid", parent=lp,
                     level=log.NOISY)
            # We need an upload helper. Check our active uploads again in
            # case there was a race.
            if storage_index in self._active_uploads:
                self.log("upload is currently active", parent=lp)
                uh = self._active_uploads[storage_index]
            else:
                self.log("creating new upload helper", parent=lp)
                uh = self.chk_upload_helper_class(storage_index, self,
                                                  incoming_file, encoding_file,
                                                  r, lp)
                self._active_uploads[storage_index] = uh
                self._add_upload(uh)
            return uh.start()
        d.addCallback(_checked)
        def _err(f):
            self.log("error while checking for chk-already-in-grid",
                     failure=f, level=log.WEIRD, parent=lp, umid="jDtxZg")
            return f
        d.addErrback(_err)
        return d

    def _check_for_chk_already_in_grid(self, storage_index, results, lp):
        # see if this file is already in the grid
        lp2 = self.log("doing a quick check+UEBfetch",
                       parent=lp, level=log.NOISY)
        sb = self.parent.get_storage_broker()
        c = CHKCheckerAndUEBFetcher(sb.get_servers, storage_index, lp2)
        d = c.check()
        def _checked(res):
            if res:
                (sharemap, ueb_data, ueb_hash) = res
                self.log("found file in grid", level=log.NOISY, parent=lp)
                results.uri_extension_hash = ueb_hash
                results.sharemap = sharemap
                results.uri_extension_data = ueb_data
                results.preexisting_shares = len(sharemap)
                results.pushed_shares = 0
                return True
            return False
        d.addCallback(_checked)
        return d

    def _add_upload(self, uh):
        self._all_uploads[uh] = None
        s = uh.get_upload_status()
        self._all_upload_statuses[s] = None
        self._recent_upload_statuses.append(s)
        while len(self._recent_upload_statuses) > self.MAX_UPLOAD_STATUSES:
            self._recent_upload_statuses.pop(0)

    def upload_finished(self, storage_index, size):
        # this is called with size=0 if the upload failed
        self.count("chk_upload_helper.encoded_bytes", size)
        uh = self._active_uploads[storage_index]
        del self._active_uploads[storage_index]
        s = uh.get_upload_status()
        s.set_active(False)

    def get_all_upload_statuses(self):
        return self._all_upload_statuses
