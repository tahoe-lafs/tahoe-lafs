
import os.path, stat
from zope.interface import implements
from twisted.application import service
from twisted.internet import defer
from foolscap import Referenceable
from allmydata import upload, interfaces
from allmydata.util import idlib, log, observer, fileutil


class NotEnoughWritersError(Exception):
    pass


class CHKUploadHelper(Referenceable, upload.CHKUploader):
    """I am the helper-server -side counterpart to AssistedUploader. I handle
    peer selection, encoding, and share pushing. I read ciphertext from the
    remote AssistedUploader.
    """
    implements(interfaces.RICHKUploadHelper)

    def __init__(self, storage_index, helper,
                 incoming_file, encoding_file,
                 log_number):
        self._storage_index = storage_index
        self._helper = helper
        self._incoming_file = incoming_file
        self._encoding_file = encoding_file
        upload_id = idlib.b2a(storage_index)[:6]
        self._log_number = log_number
        self._helper.log("CHKUploadHelper starting for SI %s" % upload_id,
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
        # determine if we need to upload the file. If so, return ({},self) .
        # If not, return (UploadResults,None) .
        self.log("deciding whether to upload the file or not", level=log.NOISY)
        if os.path.exists(self._encoding_file):
            # we have the whole file, and we're currently encoding it. The
            # caller will get to see the results when we're done. TODO: how
            # should they get upload progress in this case?
            self.log("encoding in progress", level=log.UNUSUAL)
            return self._finished_observers.when_fired()
        if os.path.exists(self._incoming_file):
            # we have some of the file, but not all of it (otherwise we'd be
            # encoding). The caller might be useful.
            self.log("partial ciphertext already present", level=log.UNUSUAL)
            return ({}, self)
        # we don't remember uploading this file, but it might already be in
        # the grid. For now we do an unconditional upload. TODO: Do a quick
        # checker run (send one query to each storage server) to see who has
        # the file. Then accomodate a lazy uploader by retrieving the UEB
        # from one of the shares and hash it.
        #return ({'uri_extension_hash': hashutil.uri_extension_hash("")},self)
        self.log("no record of having uploaded the file", level=log.NOISY)
        return ({}, self)

    def remote_upload(self, reader):
        # reader is an RIEncryptedUploadable. I am specified to return an
        # UploadResults dictionary.

        if os.path.exists(self._encoding_file):
            # we've already started encoding, so we have no use for the
            # reader. Notify them when we're done.
            return self._finished_observers.when_fired()

        # let our fetcher pull ciphertext from the reader.
        self._fetcher.add_reader(reader)
        # and also hashes
        self._reader.add_reader(reader)

        # and inform the client when the upload has finished
        return self._finished_observers.when_fired()

    def _finished(self, res):
        (uri_extension_hash, needed_shares, total_shares, size) = res
        upload_results = {'uri_extension_hash': uri_extension_hash}
        self._reader.close()
        os.unlink(self._encoding_file)
        self._finished_observers.fire(upload_results)
        self._helper.upload_finished(self._storage_index)
        del self._reader

    def _failed(self, f):
        self._finished_observers.fire(f)
        self._helper.upload_finished(self._storage_index)
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
        self._log_parent = logparent
        self._done_observers = observer.OneShotObserverList()
        self._readers = []
        self._started = False
        self._f = None

    def log(self, *args, **kwargs):
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.helper.chkupload.fetch"
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_parent
        return log.msg(*args, **kwargs)

    def add_reader(self, reader):
        AskUntilSuccessMixin.add_reader(self, reader)
        self._start()

    def _start(self):
        if self._started:
            return
        self._started = True

        # first, find out how large the file is going to be
        d = self.call("get_size")
        d.addCallback(self._got_size)
        d.addCallback(self._start_reading)
        d.addCallback(self._done)
        d.addErrback(self._failed)

    def _got_size(self, size):
        self.log("total size is %d bytes" % size, level=log.NOISY)
        self._expected_size = size

    def _start_reading(self, res):
        # then find out how much crypttext we have on disk
        if os.path.exists(self._incoming_file):
            self._have = os.stat(self._incoming_file)[stat.ST_SIZE]
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
        d = defer.maybeDeferred(self._fetch)
        def _done(finished):
            if finished:
                self.log("finished reading ciphertext", level=log.NOISY)
                fire_when_done.callback(None)
            else:
                self._loop(fire_when_done)
        def _err(f):
            self.log("ciphertext read failed", failure=f, level=log.UNUSUAL)
            fire_when_done.errback(f)
        d.addCallbacks(_done, _err)
        return None

    def _fetch(self):
        needed = self._expected_size - self._have
        fetch_size = min(needed, self.CHUNK_SIZE)
        if fetch_size == 0:
            return True # all done
        self.log(format="fetching %(start)d-%(end)d of %(total)d",
                 start=self._have,
                 end=self._have+fetch_size,
                 total=self._expected_size,
                 level=log.NOISY)
        d = self.call("read_encrypted", self._have, fetch_size)
        def _got_data(ciphertext_v):
            for data in ciphertext_v:
                self._f.write(data)
                self._have += len(data)
            return False # not done
        d.addCallback(_got_data)
        return d

    def _done(self, res):
        self._f.close()
        self._f = None
        self._readers = []
        self.log(format="done fetching ciphertext, size=%(size)d",
                 size=os.stat(self._incoming_file)[stat.ST_SIZE],
                 level=log.NOISY)
        os.rename(self._incoming_file, self._encoding_file)
        self._done_observers.fire(None)

    def _failed(self, f):
        if self._f:
            self._f.close()
        self._readers = []
        self._done_observers.fire(f)

    def when_done(self):
        return self._done_observers.when_fired()



class LocalCiphertextReader(AskUntilSuccessMixin):
    implements(interfaces.IEncryptedUploadable)

    def __init__(self, upload_helper, storage_index, encoding_file):
        self._readers = []
        self._upload_helper = upload_helper
        self._storage_index = storage_index
        self._encoding_file = encoding_file

    def start(self):
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
    def get_plaintext_hashtree_leaves(self, first, last, num_segments):
        return self.call("get_plaintext_hashtree_leaves", first, last,
                         num_segments)
    def get_plaintext_hash(self):
        return self.call("get_plaintext_hash")
    def close(self):
        self.f.close()
        # ??. I'm not sure if it makes sense to forward the close message.
        return self.call("close")



class Helper(Referenceable, service.MultiService):
    implements(interfaces.RIHelper)
    # this is the non-distributed version. When we need to have multiple
    # helpers, this object will become the HelperCoordinator, and will query
    # the farm of Helpers to see if anyone has the storage_index of interest,
    # and send the request off to them. If nobody has it, we'll choose a
    # helper at random.

    name = "helper"
    chk_upload_helper_class = CHKUploadHelper

    def __init__(self, basedir):
        self._basedir = basedir
        self._chk_incoming = os.path.join(basedir, "CHK_incoming")
        self._chk_encoding = os.path.join(basedir, "CHK_encoding")
        fileutil.make_dirs(self._chk_incoming)
        fileutil.make_dirs(self._chk_encoding)
        self._active_uploads = {}
        service.MultiService.__init__(self)

    def log(self, *args, **kwargs):
        if 'facility' not in kwargs:
            kwargs['facility'] = "tahoe.helper"
        return self.parent.log(*args, **kwargs)

    def remote_upload_chk(self, storage_index):
        si_s = idlib.b2a(storage_index)
        lp = self.log(format="helper: upload_chk query for SI %(si)s", si=si_s)
        incoming_file = os.path.join(self._chk_incoming, si_s)
        encoding_file = os.path.join(self._chk_encoding, si_s)
        if storage_index in self._active_uploads:
            self.log("upload is currently active", parent=lp)
            uh = self._active_uploads[storage_index]
        else:
            self.log("creating new upload helper", parent=lp)
            uh = self.chk_upload_helper_class(storage_index, self,
                                              incoming_file, encoding_file,
                                              lp)
            self._active_uploads[storage_index] = uh
        return uh.start()

    def upload_finished(self, storage_index):
        del self._active_uploads[storage_index]
