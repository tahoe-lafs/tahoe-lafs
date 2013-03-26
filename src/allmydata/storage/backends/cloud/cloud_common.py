
from collections import deque
from cStringIO import StringIO
import urllib

from twisted.internet import defer, reactor, task
from twisted.python.failure import Failure
from twisted.web.error import Error
from twisted.web.client import FileBodyProducer, ResponseDone, Agent, HTTPConnectionPool
from twisted.web.http_headers import Headers
from twisted.internet.protocol import Protocol
from twisted.internet.error import TimeoutError

from zope.interface import Interface, implements
from allmydata.interfaces import IShareBase

from allmydata.util import log
from allmydata.util.assertutil import precondition, _assert
from allmydata.util.deferredutil import eventually_callback, eventually_errback, eventual_chain, gatherResults
from allmydata.storage.common import si_b2a, NUM_RE


# The container has keys of the form shares/$PREFIX/$STORAGEINDEX/$SHNUM.$CHUNK

def get_share_key(si, shnum=None):
    sistr = si_b2a(si)
    if shnum is None:
        return "shares/%s/%s/" % (sistr[:2], sistr)
    else:
        return "shares/%s/%s/%d" % (sistr[:2], sistr, shnum)

def get_chunk_key(share_key, chunknum):
    precondition(chunknum >= 0, chunknum=chunknum)
    if chunknum == 0:
        return share_key
    else:
        return "%s.%d" % (share_key, chunknum)


PREFERRED_CHUNK_SIZE = 512*1024
PIPELINE_DEPTH = 4

ZERO_CHUNKDATA = "\x00"*PREFERRED_CHUNK_SIZE

def get_zero_chunkdata(size):
    if size <= PREFERRED_CHUNK_SIZE:
        return ZERO_CHUNKDATA[: size]
    else:
        return "\x00"*size


class IContainer(Interface):
    """
    I represent a cloud container.
    """
    def create():
        """
        Create this container.
        """

    def delete():
        """
        Delete this container.
        The cloud service may require the container to be empty before it can be deleted.
        """

    def list_objects(prefix=''):
        """
        Get a ContainerListing that lists objects in this container.

        prefix: (str) limit the returned keys to those starting with prefix.
        """

    def put_object(object_name, data, content_type=None, metadata={}):
        """
        Put an object in this bucket.
        Any existing object of the same name will be replaced.
        """

    def get_object(object_name):
        """
        Get an object from this container.
        """

    def head_object(object_name):
        """
        Retrieve object metadata only.
        """

    def delete_object(object_name):
        """
        Delete an object from this container.
        Once deleted, there is no method to restore or undelete an object.
        """


def delete_chunks(container, share_key, from_chunknum=0):
    d = container.list_objects(prefix=share_key)
    def _delete(res):
        def _suppress_404(f):
            e = f.trap(container.ServiceError)
            if e.get_error_code() != 404:
                return f

        d2 = defer.succeed(None)
        for item in res.contents:
            key = item.key
            _assert(key.startswith(share_key), key=key, share_key=share_key)
            path = key.split('/')
            if len(path) == 4:
                (_, _, chunknumstr) = path[3].partition('.')
                chunknumstr = chunknumstr or "0"
                if NUM_RE.match(chunknumstr) and int(chunknumstr) >= from_chunknum:
                    d2.addCallback(lambda ign, key=key: container.delete_object(key))
                    d2.addErrback(_suppress_404)
        return d2
    d.addCallback(_delete)
    return d


class CloudShareBase(object):
    implements(IShareBase)
    """
    Attributes:
      _container:     (IContainer) the cloud container that stores this share
      _storage_index: (str) binary storage index
      _shnum:         (integer) share number
      _key:           (str) the key prefix under which this share will be stored (no .chunknum suffix)
      _data_length:   (integer) length of data excluding headers and leases
      _total_size:    (integer) total size of the sharefile

    Methods:
      _discard(self): object will no longer be used; discard references to potentially large data
    """
    def __init__(self, container, storage_index, shnum):
        precondition(IContainer.providedBy(container), container=container)
        precondition(isinstance(storage_index, str), storage_index=storage_index)
        precondition(isinstance(shnum, int), shnum=shnum)

        # These are always known immediately.
        self._container = container
        self._storage_index = storage_index
        self._shnum = shnum
        self._key = get_share_key(storage_index, shnum)

        # Subclasses must set _data_length and _total_size.

    def __repr__(self):
        return ("<%s at %r key %r>" % (self.__class__.__name__, self._container, self._key,))

    def get_storage_index(self):
        return self._storage_index

    def get_storage_index_string(self):
        return si_b2a(self._storage_index)

    def get_shnum(self):
        return self._shnum

    def get_data_length(self):
        return self._data_length

    def get_size(self):
        return self._total_size

    def get_used_space(self):
        # We're not charged for any per-object overheads in supported cloud services, so
        # total object data sizes are what we're interested in for statistics and accounting.
        return self.get_size()

    def unlink(self):
        self._discard()
        return delete_chunks(self._container, self._key)

    def _get_path(self):
        """
        When used with the mock cloud container, this returns the path of the file containing
        the first chunk. For a real cloud container, it raises an error.
        """
        # It is OK that _get_path doesn't exist on real cloud container objects.
        return self._container._get_path(self._key)


class CloudShareReaderMixin:
    """
    Attributes:
      _data_length: (integer) length of data excluding headers and leases
      _chunksize:   (integer) size of each chunk possibly excluding the last
      _cache:       (ChunkCache) the cache used to read chunks

      DATA_OFFSET:  (integer) offset to the start-of-data from start of the sharefile
    """
    def readv(self, readv):
        sorted_readv = sorted(zip(readv, xrange(len(readv))))
        datav = [None]*len(readv)
        for (v, i) in sorted_readv:
            (offset, length) = v
            datav[i] = self.read_share_data(offset, length)
        return gatherResults(datav)

    def read_share_data(self, offset, length):
        precondition(offset >= 0)

        # Reads beyond the end of the data are truncated.
        # Reads that start beyond the end of the data return an empty string.
        seekpos = self.DATA_OFFSET + offset
        actuallength = max(0, min(length, self._data_length - offset))
        if actuallength == 0:
            return defer.succeed("")

        lastpos = seekpos + actuallength - 1
        _assert(lastpos > 0, seekpos=seekpos, actuallength=actuallength, lastpos=lastpos)
        start_chunknum = seekpos / self._chunksize
        start_offset   = seekpos % self._chunksize
        last_chunknum  = lastpos / self._chunksize
        last_offset    = lastpos % self._chunksize
        _assert(start_chunknum <= last_chunknum, start_chunknum=start_chunknum, last_chunknum=last_chunknum)

        parts = deque()

        def _load_part(ign, chunknum):
            # determine which part of this chunk we need
            start = 0
            end = self._chunksize
            if chunknum == start_chunknum:
                start = start_offset
            if chunknum == last_chunknum:
                end = last_offset + 1
            #print "LOAD", get_chunk_key(self._key, chunknum), start, end

            # d2 fires when we should continue loading the next chunk; chunkdata_d fires with the actual data.
            chunkdata_d = defer.Deferred()
            d2 = self._cache.get(chunknum, chunkdata_d)
            if start > 0 or end < self._chunksize:
                chunkdata_d.addCallback(lambda chunkdata: chunkdata[start : end])
            parts.append(chunkdata_d)
            return d2

        d = defer.succeed(None)
        for i in xrange(start_chunknum, last_chunknum + 1):
            d.addCallback(_load_part, i)
        d.addCallback(lambda ign: gatherResults(parts))
        d.addCallback(lambda pieces: ''.join(pieces))
        return d


class CloudError(Exception):
    pass


class CloudServiceError(Error):
    """
    A error class similar to txaws' S3Error.
    """
    def __init__(self, xml_bytes, status, message=None, response=None, request_id="", host_id=""):
        Error.__init__(self, status, message, response)
        self.original = xml_bytes
        self.status = str(status)
        self.message = str(message)
        self.request_id = request_id
        self.host_id = host_id

    def get_error_code(self):
        return self.status

    def get_error_message(self):
        return self.message

    def parse(self, xml_bytes=""):
        raise NotImplementedError

    def has_error(self, errorString):
        raise NotImplementedError

    def get_error_codes(self):
        raise NotImplementedError

    def get_error_messages(self):
        raise NotImplementedError


# Originally from txaws.s3.model (under different class names), which was under the MIT / Expat licence.

class ContainerItem(object):
    """
    An item in a listing of cloud objects.
    """
    def __init__(self, key, modification_date, etag, size, storage_class,
                 owner=None):
        self.key = key
        self.modification_date = modification_date
        self.etag = etag
        self.size = size
        self.storage_class = storage_class
        self.owner = owner

    def __repr__(self):
        return "<ContainerItem %r>" % ({
                   "key": self.key,
                   "modification_date": self.modification_date,
                   "etag": self.etag,
                   "size": self.size,
                   "storage_class": self.storage_class,
                   "owner": self.owner,
               },)


class ContainerListing(object):
    def __init__(self, name, prefix, marker, max_keys, is_truncated,
                 contents=None, common_prefixes=None):
        precondition(isinstance(is_truncated, str))
        self.name = name
        self.prefix = prefix
        self.marker = marker
        self.max_keys = max_keys
        self.is_truncated = is_truncated
        self.contents = contents
        self.common_prefixes = common_prefixes

    def __repr__(self):
        return "<ContainerListing %r>" % ({
                   "name": self.name,
                   "prefix": self.prefix,
                   "marker": self.marker,
                   "max_keys": self.max_keys,
                   "is_truncated": self.is_truncated,
                   "contents": self.contents,
                   "common_prefixes": self.common_prefixes,
               })


BACKOFF_SECONDS_BEFORE_RETRY = (0, 2, 10)


class ContainerRetryMixin:
    """
    I provide a helper method for performing an operation on a cloud container that will retry up to
    len(BACKOFF_SECONDS_FOR_RETRY) times (not including the initial try). If the initial try fails, a
    single incident will be triggered after the operation has succeeded or failed.

    Subclasses should define:
      ServiceError:
          An exceptions class with meaningful status codes that can be
          filtered using _react_to_error. Other exceptions will cause
          unconditional retries.

    and can override:
      _react_to_error(self, response_code):
          Returns True if the error should be retried. May perform side effects before the retry.
    """

    def _react_to_error(self, response_code):
        # The default policy is to retry on 5xx errors.
        return response_code >= 500 and response_code < 600

    def _do_request(self, description, operation, *args, **kwargs):
        d = defer.maybeDeferred(operation, *args, **kwargs)
        def _retry(f):
            d2 = self._handle_error(f, 1, None, description, operation, *args, **kwargs)
            def _trigger_incident(res):
                log.msg(format="error(s) on cloud container operation: %(description)s %(arguments)s %(kwargs)s %(res)s",
                        arguments=args[:2], kwargs=kwargs, description=description, res=res,
                        level=log.WEIRD)
                return res
            d2.addBoth(_trigger_incident)
            return d2
        d.addErrback(_retry)
        return d

    def _handle_error(self, f, trynum, first_err_and_tb, description, operation, *args, **kwargs):
        # Don't use f.getTracebackObject() since a fake traceback will not do for the 3-arg form of 'raise'.
        # tb can be None (which is acceptable for 3-arg raise) if we don't have a traceback.
        tb = getattr(f, 'tb', None)
        fargs = f.value.args
        if len(fargs) > 2 and fargs[2] and '<code>signaturedoesnotmatch</code>' in fargs[2].lower():
            fargs = fargs[:2] + ("SignatureDoesNotMatch response redacted",) + fargs[3:]

        args_without_data = args[:2]
        msg = "try %d failed: %s %s %s" % (trynum, description, args_without_data, kwargs)
        err = CloudError(msg, *fargs)

        # This should not trigger an incident; we want to do that at the end.
        log.msg(format="try %(trynum)d failed: %(description)s %(arguments)s %(kwargs)s %(ftype)s %(fargs)s",
                trynum=trynum, arguments=args_without_data, kwargs=kwargs, description=description, ftype=str(f.value.__class__), fargs=repr(fargs),
                level=log.INFREQUENT)

        if first_err_and_tb is None:
            first_err_and_tb = (err, tb)

        if trynum > len(BACKOFF_SECONDS_BEFORE_RETRY):
            # If we run out of tries, raise the error we got on the first try (which *may* have
            # a more useful traceback).
            (first_err, first_tb) = first_err_and_tb
            raise first_err.__class__, first_err, first_tb

        retry = True
        if f.check(self.ServiceError):
            fargs = f.value.args
            if len(fargs) > 0:
                retry = self._react_to_error(int(fargs[0]))
            else:
                retry = False

        if retry:
            d = task.deferLater(self._reactor, BACKOFF_SECONDS_BEFORE_RETRY[trynum-1], operation, *args, **kwargs)
            d.addErrback(self._handle_error, trynum+1, first_err_and_tb, description, operation, *args, **kwargs)
            return d

        # If we get an error response for which _react_to_error says we should not retry,
        # raise that error even if the request was itself a retry.
        raise err.__class__, err, tb


def concat(seqs):
    """
    O(n), rather than O(n^2), concatenation of list-like things, returning a list.
    I can't believe this isn't built in.
    """
    total_len = 0
    for seq in seqs:
        total_len += len(seq)
    result = [None]*total_len
    i = 0
    for seq in seqs:
        for x in seq:
            result[i] = x
            i += 1
    _assert(i == total_len, i=i, total_len=total_len)
    return result


class ContainerListMixin:
    """
    S3 has a limitation of 1000 object entries returned on each list (GET Bucket) request.
    I provide a helper method to repeat the call as many times as necessary to get a full
    listing. The container is assumed to implement:

    def list_some_objects(self, **kwargs):
        # kwargs may include 'prefix' and 'marker' parameters as documented at
        # <http://docs.amazonwebservices.com/AmazonS3/latest/API/RESTBucketGET.html>.
        # returns Deferred ContainerListing

    Note that list_some_objects is assumed to be reliable; so, if retries are needed,
    the container class should also inherit from ContainerRetryMixin and list_some_objects
    should make the request via _do_request.

    The 'delimiter' parameter of the GET Bucket API is not supported.
    """
    def list_objects(self, prefix=''):
        kwargs = {'prefix': prefix}
        all_contents = deque()
        def _list_some():
            d2 = self.list_some_objects(**kwargs)
            def _got_listing(res):
                all_contents.append(res.contents)
                if res.is_truncated == "true":
                    _assert(len(res.contents) > 0)
                    marker = res.contents[-1].key
                    _assert('marker' not in kwargs or marker > kwargs['marker'],
                            "Not making progress in list_objects", kwargs=kwargs, marker=marker)
                    kwargs['marker'] = marker
                    return _list_some()
                else:
                    _assert(res.is_truncated == "false", is_truncated=res.is_truncated)
                    return res
            d2.addCallback(_got_listing)
            return d2

        d = _list_some()
        d.addCallback(lambda res: res.__class__(res.name, res.prefix, res.marker, res.max_keys,
                                                "false", concat(all_contents)))
        def _log(f):
            log.msg(f, level=log.WEIRD)
            return f
        d.addErrback(_log)
        return d


class BackpressurePipeline(object):
    """
    I manage a pipeline of Deferred operations that allows the data source to feel backpressure
    when the pipeline is "full". I do not actually limit the number of operations in progress.
    """
    OPEN = 0
    CLOSING = 1
    CLOSED = 2

    def __init__(self, capacity):
        self._capacity = capacity  # how full we can be before causing calls to 'add' to block
        self._gauge = 0            # how full we are
        self._waiting = []         # callers of add() who are blocked
        self._unfinished = 0       # number of pending operations
        self._result_d = defer.Deferred()
        self._state = self.OPEN

    def add(self, _size, _func, *args, **kwargs):
        if self._state == self.CLOSED:
            msg = "add() called on closed BackpressurePipeline"
            log.err(msg, level=log.WEIRD)
            def _already_closed(): raise AssertionError(msg)
            return defer.execute(_already_closed)
        self._gauge += _size
        self._unfinished += 1
        fd = defer.maybeDeferred(_func, *args, **kwargs)
        fd.addBoth(self._call_finished, _size)
        fd.addErrback(log.err, "BackpressurePipeline._call_finished raised an exception")
        if self._gauge < self._capacity:
            return defer.succeed(None)
        d = defer.Deferred()
        self._waiting.append(d)
        return d

    def fail(self, f):
        if self._state != self.CLOSED:
            self._state = self.CLOSED
            eventually_errback(self._result_d)(f)

    def flush(self):
        if self._state == self.CLOSED:
            return defer.succeed(self._result_d)

        d = self.close()
        d.addBoth(self.reopen)
        return d

    def close(self):
        if self._state != self.CLOSED:
            if self._unfinished == 0:
                self._state = self.CLOSED
                eventually_callback(self._result_d)(None)
            else:
                self._state = self.CLOSING
        return self._result_d

    def reopen(self, res=None):
        _assert(self._state == self.CLOSED, state=self._state)
        self._result_d = defer.Deferred()
        self._state = self.OPEN
        return res

    def _call_finished(self, res, size):
        self._unfinished -= 1
        self._gauge -= size
        if isinstance(res, Failure):
            self.fail(res)

        if self._state == self.CLOSING:
            # repeat the unfinished == 0 check
            self.close()

        if self._state == self.CLOSED:
            while self._waiting:
                d = self._waiting.pop(0)
                eventual_chain(self._result_d, d)
        elif self._gauge < self._capacity:
            while self._waiting:
                d = self._waiting.pop(0)
                eventually_callback(d)(None)
        return None


class ChunkCache(object):
    """I cache chunks for a specific share object."""

    def __init__(self, container, key, chunksize, nchunks=1, initial_cachemap={}):
        self._container = container
        self._key = key
        self._chunksize = chunksize
        self._nchunks = nchunks

        # chunknum -> deferred data
        self._cachemap = initial_cachemap
        self._pipeline = BackpressurePipeline(PIPELINE_DEPTH)

    def set_nchunks(self, nchunks):
        self._nchunks = nchunks

    def _load_chunk(self, chunknum, chunkdata_d):
        d = self._container.get_object(get_chunk_key(self._key, chunknum))
        eventual_chain(source=d, target=chunkdata_d)
        return d

    def get(self, chunknum, result_d):
        if chunknum in self._cachemap:
            # cache hit; never stall
            eventual_chain(source=self._cachemap[chunknum], target=result_d)
            return defer.succeed(None)

        # Evict any chunks other than the first and last two, until there are
        # three or fewer chunks left cached.
        for candidate_chunknum in self._cachemap.keys():
            if len(self._cachemap) <= 3:
                break
            if candidate_chunknum not in (0, self._nchunks-2, self._nchunks-1):
                self.flush_chunk(candidate_chunknum)

        # cache miss; stall when the pipeline is full
        chunkdata_d = defer.Deferred()
        d = self._pipeline.add(1, self._load_chunk, chunknum, chunkdata_d)
        def _check(res):
            _assert(res is not None)
            return res
        chunkdata_d.addCallback(_check)
        self._cachemap[chunknum] = chunkdata_d
        eventual_chain(source=chunkdata_d, target=result_d)
        return d

    def flush_chunk(self, chunknum):
        if chunknum in self._cachemap:
            del self._cachemap[chunknum]

    def close(self):
        self._cachemap = None
        return self._pipeline.close()


class Discard(Protocol):
    # see http://twistedmatrix.com/trac/ticket/5488
    def makeConnection(self, producer):
        producer.stopProducing()


class DataCollector(Protocol):
    def __init__(self, ServiceError):
        self._data = deque()
        self._done = defer.Deferred()
        self.ServiceError = ServiceError

    def dataReceived(self, bytes):
        self._data.append(bytes)

    def connectionLost(self, reason):
        if reason.check(ResponseDone):
            eventually_callback(self._done)("".join(self._data))
        else:
            def _failed(): raise self.ServiceError(None, 0, message=reason.getErrorMessage())
            eventually_errback(self._done)(defer.execute(_failed))

    def when_done(self):
        """CAUTION: this always returns the same Deferred."""
        return self._done


class HTTPClientMixin:
    """
    I implement helper methods for making HTTP requests and getting response headers.

    Subclasses should define:
      _agent:
          The instance of twisted.web.client.Agent to be used.
      USER_AGENT:
          User agent string.
      ServiceError:
          The error class to trap (CloudServiceError or similar).
    """
    def _http_request(self, what, method, url, request_headers, body=None, need_response_body=False):
        # Agent.request adds a Host header automatically based on the URL.
        request_headers['User-Agent'] = [self.USER_AGENT]

        if body is None:
            bodyProducer = None
        else:
            bodyProducer = FileBodyProducer(StringIO(body))
            # We don't need to explicitly set Content-Length because FileBodyProducer knows the length
            # (and if we do it won't work, because in that case Content-Length would be duplicated).

        log.msg(format="%(what)s request: %(method)s %(url)s %(header_keys)s",
                what=what, method=method, url=url, header_keys=repr(request_headers.keys()), level=log.OPERATIONAL)

        d = defer.maybeDeferred(self._agent.request, method, url, Headers(request_headers), bodyProducer)

        def _got_response(response):
            log.msg(format="%(what)s response: %(code)d %(phrase)s",
                    what=what, code=response.code, phrase=response.phrase, level=log.OPERATIONAL)

            if response.code < 200 or response.code >= 300:
                raise self.ServiceError(None, response.code,
                                        message="unexpected response code %r %s" % (response.code, response.phrase))

            if need_response_body:
                collector = DataCollector(self.ServiceError)
                response.deliverBody(collector)
                d2 = collector.when_done()
                d2.addCallback(lambda body: (response, body))
                return d2
            else:
                response.deliverBody(Discard())
                return (response, None)
        d.addCallback(_got_response)
        return d

    def _get_header(self, response, name):
        hs = response.headers.getRawHeaders(name)
        if len(hs) == 0:
            raise self.ServiceError(None, response.code,
                                    message="missing response header %r" % (name,))
        return hs[0]



class CommonContainerMixin(HTTPClientMixin, ContainerRetryMixin):
    """
    Base class for cloud storage providers with similar APIs.

    In particular, OpenStack and Google Storage are very similar (presumably
    since they both copy S3).
    """

    def __init__(self, container_name, override_reactor=None):
        self._container_name = container_name
        self._reactor = override_reactor or reactor
        pool = HTTPConnectionPool(self._reactor)
        pool.maxPersistentPerHost = 20
        self._agent = Agent(self._reactor, connectTimeout=10, pool=pool)
        self.ServiceError = CloudServiceError

    def __repr__(self):
        return ("<%s %r>" % (self.__class__.__name__, self._container_name,))

    def _make_container_url(self, public_storage_url):
        return "%s/%s" % (public_storage_url, urllib.quote(self._container_name, safe=''))

    def _make_object_url(self, public_storage_url, object_name):
        return "%s/%s/%s" % (public_storage_url, urllib.quote(self._container_name, safe=''),
                             urllib.quote(object_name))

    def create(self):
        return self._do_request('create container', self._create)

    def delete(self):
        return self._do_request('delete container', self._delete)

    def list_objects(self, prefix=''):
        return self._do_request('list objects', self._list_objects, prefix)

    def put_object(self, object_name, data, content_type='application/octet-stream', metadata={}):
        return self._do_request('PUT object', self._put_object, object_name, data, content_type, metadata)

    def get_object(self, object_name):
        return self._do_request('GET object', self._get_object, object_name)

    def head_object(self, object_name):
        return self._do_request('HEAD object', self._head_object, object_name)

    def delete_object(self, object_name):
        return self._do_request('DELETE object', self._delete_object, object_name)
