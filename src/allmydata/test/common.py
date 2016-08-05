import os, random, struct
from zope.interface import implements
from twisted.internet import defer
from twisted.internet.interfaces import IPullProducer
from twisted.python import failure
from twisted.application import service
from twisted.web.error import Error as WebError
from allmydata import uri
from allmydata.interfaces import IMutableFileNode, IImmutableFileNode,\
                                 NotEnoughSharesError, ICheckable, \
                                 IMutableUploadable, SDMF_VERSION, \
                                 MDMF_VERSION
from allmydata.check_results import CheckResults, CheckAndRepairResults, \
     DeepCheckResults, DeepCheckAndRepairResults
from allmydata.storage_client import StubServer
from allmydata.mutable.layout import unpack_header
from allmydata.mutable.publish import MutableData
from allmydata.storage.mutable import MutableShareFile
from allmydata.util import hashutil, log
from allmydata.util.assertutil import precondition
from allmydata.util.consumer import download_to_data
import allmydata.test.common_util as testutil
from allmydata.immutable.upload import Uploader

TEST_RSA_KEY_SIZE = 522

class DummyProducer:
    implements(IPullProducer)
    def resumeProducing(self):
        pass

class FakeCHKFileNode:
    """I provide IImmutableFileNode, but all of my data is stored in a
    class-level dictionary."""
    implements(IImmutableFileNode)

    def __init__(self, filecap, all_contents):
        precondition(isinstance(filecap, (uri.CHKFileURI, uri.LiteralFileURI)), filecap)
        self.all_contents = all_contents
        self.my_uri = filecap
        self.storage_index = self.my_uri.get_storage_index()

    def get_uri(self):
        return self.my_uri.to_string()
    def get_write_uri(self):
        return None
    def get_readonly_uri(self):
        return self.my_uri.to_string()
    def get_cap(self):
        return self.my_uri
    def get_verify_cap(self):
        return self.my_uri.get_verify_cap()
    def get_repair_cap(self):
        return self.my_uri.get_verify_cap()
    def get_storage_index(self):
        return self.storage_index

    def check(self, monitor, verify=False, add_lease=False):
        s = StubServer("\x00"*20)
        r = CheckResults(self.my_uri, self.storage_index,
                         healthy=True, recoverable=True,
                         count_happiness=10,
                         count_shares_needed=3,
                         count_shares_expected=10,
                         count_shares_good=10,
                         count_good_share_hosts=10,
                         count_recoverable_versions=1,
                         count_unrecoverable_versions=0,
                         servers_responding=[s],
                         sharemap={1: [s]},
                         count_wrong_shares=0,
                         list_corrupt_shares=[],
                         count_corrupt_shares=0,
                         list_incompatible_shares=[],
                         count_incompatible_shares=0,
                         summary="",
                         report=[],
                         share_problems=[],
                         servermap=None)
        return defer.succeed(r)
    def check_and_repair(self, monitor, verify=False, add_lease=False):
        d = self.check(verify)
        def _got(cr):
            r = CheckAndRepairResults(self.storage_index)
            r.pre_repair_results = r.post_repair_results = cr
            return r
        d.addCallback(_got)
        return d

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

    def get_size(self):
        if isinstance(self.my_uri, uri.LiteralFileURI):
            return self.my_uri.get_size()
        try:
            data = self.all_contents[self.my_uri.to_string()]
        except KeyError, le:
            raise NotEnoughSharesError(le, 0, 3)
        return len(data)
    def get_current_size(self):
        return defer.succeed(self.get_size())

    def read(self, consumer, offset=0, size=None):
        # we don't bother to call registerProducer/unregisterProducer,
        # because it's a hassle to write a dummy Producer that does the right
        # thing (we have to make sure that DummyProducer.resumeProducing
        # writes the data into the consumer immediately, otherwise it will
        # loop forever).

        d = defer.succeed(None)
        d.addCallback(self._read, consumer, offset, size)
        return d

    def _read(self, ignored, consumer, offset, size):
        if isinstance(self.my_uri, uri.LiteralFileURI):
            data = self.my_uri.data
        else:
            if self.my_uri.to_string() not in self.all_contents:
                raise NotEnoughSharesError(None, 0, 3)
            data = self.all_contents[self.my_uri.to_string()]
        start = offset
        if size is not None:
            end = offset + size
        else:
            end = len(data)
        consumer.write(data[start:end])
        return consumer


    def get_best_readable_version(self):
        return defer.succeed(self)


    def download_to_data(self, progress=None):
        return download_to_data(self, progress=progress)


    download_best_version = download_to_data


    def get_size_of_best_version(self):
        return defer.succeed(self.get_size)


def make_chk_file_cap(size):
    return uri.CHKFileURI(key=os.urandom(16),
                          uri_extension_hash=os.urandom(32),
                          needed_shares=3,
                          total_shares=10,
                          size=size)
def make_chk_file_uri(size):
    return make_chk_file_cap(size).to_string()

def create_chk_filenode(contents, all_contents):
    filecap = make_chk_file_cap(len(contents))
    n = FakeCHKFileNode(filecap, all_contents)
    all_contents[filecap.to_string()] = contents
    return n


class FakeMutableFileNode:
    """I provide IMutableFileNode, but all of my data is stored in a
    class-level dictionary."""

    implements(IMutableFileNode, ICheckable)
    MUTABLE_SIZELIMIT = 10000

    def __init__(self, storage_broker, secret_holder,
                 default_encoding_parameters, history, all_contents):
        self.all_contents = all_contents
        self.file_types = {} # storage index => MDMF_VERSION or SDMF_VERSION
        self.init_from_cap(make_mutable_file_cap())
        self._k = default_encoding_parameters['k']
        self._segsize = default_encoding_parameters['max_segment_size']
    def create(self, contents, key_generator=None, keysize=None,
               version=SDMF_VERSION):
        if version == MDMF_VERSION and \
            isinstance(self.my_uri, (uri.ReadonlySSKFileURI,
                                 uri.WriteableSSKFileURI)):
            self.init_from_cap(make_mdmf_mutable_file_cap())
        self.file_types[self.storage_index] = version
        initial_contents = self._get_initial_contents(contents)
        data = initial_contents.read(initial_contents.get_size())
        data = "".join(data)
        self.all_contents[self.storage_index] = data
        return defer.succeed(self)
    def _get_initial_contents(self, contents):
        if contents is None:
            return MutableData("")

        if IMutableUploadable.providedBy(contents):
            return contents

        assert callable(contents), "%s should be callable, not %s" % \
               (contents, type(contents))
        return contents(self)
    def init_from_cap(self, filecap):
        assert isinstance(filecap, (uri.WriteableSSKFileURI,
                                    uri.ReadonlySSKFileURI,
                                    uri.WriteableMDMFFileURI,
                                    uri.ReadonlyMDMFFileURI))
        self.my_uri = filecap
        self.storage_index = self.my_uri.get_storage_index()
        if isinstance(filecap, (uri.WriteableMDMFFileURI,
                                uri.ReadonlyMDMFFileURI)):
            self.file_types[self.storage_index] = MDMF_VERSION

        else:
            self.file_types[self.storage_index] = SDMF_VERSION

        return self
    def get_cap(self):
        return self.my_uri
    def get_readcap(self):
        return self.my_uri.get_readonly()
    def get_uri(self):
        return self.my_uri.to_string()
    def get_write_uri(self):
        if self.is_readonly():
            return None
        return self.my_uri.to_string()
    def get_readonly(self):
        return self.my_uri.get_readonly()
    def get_readonly_uri(self):
        return self.my_uri.get_readonly().to_string()
    def get_verify_cap(self):
        return self.my_uri.get_verify_cap()
    def get_repair_cap(self):
        if self.my_uri.is_readonly():
            return None
        return self.my_uri
    def is_readonly(self):
        return self.my_uri.is_readonly()
    def is_mutable(self):
        return self.my_uri.is_mutable()
    def is_unknown(self):
        return False
    def is_allowed_in_immutable_directory(self):
        return not self.my_uri.is_mutable()
    def raise_error(self):
        pass
    def get_writekey(self):
        return "\x00"*16
    def get_size(self):
        return len(self.all_contents[self.storage_index])
    def get_current_size(self):
        return self.get_size_of_best_version()
    def get_size_of_best_version(self):
        return defer.succeed(len(self.all_contents[self.storage_index]))

    def get_storage_index(self):
        return self.storage_index

    def get_servermap(self, mode):
        return defer.succeed(None)

    def get_version(self):
        assert self.storage_index in self.file_types
        return self.file_types[self.storage_index]

    def check(self, monitor, verify=False, add_lease=False):
        s = StubServer("\x00"*20)
        r = CheckResults(self.my_uri, self.storage_index,
                         healthy=True, recoverable=True,
                         count_happiness=10,
                         count_shares_needed=3,
                         count_shares_expected=10,
                         count_shares_good=10,
                         count_good_share_hosts=10,
                         count_recoverable_versions=1,
                         count_unrecoverable_versions=0,
                         servers_responding=[s],
                         sharemap={"seq1-abcd-sh0": [s]},
                         count_wrong_shares=0,
                         list_corrupt_shares=[],
                         count_corrupt_shares=0,
                         list_incompatible_shares=[],
                         count_incompatible_shares=0,
                         summary="",
                         report=[],
                         share_problems=[],
                         servermap=None)
        return defer.succeed(r)

    def check_and_repair(self, monitor, verify=False, add_lease=False):
        d = self.check(verify)
        def _got(cr):
            r = CheckAndRepairResults(self.storage_index)
            r.pre_repair_results = r.post_repair_results = cr
            return r
        d.addCallback(_got)
        return d

    def deep_check(self, verify=False, add_lease=False):
        d = self.check(verify)
        def _done(r):
            dr = DeepCheckResults(self.storage_index)
            dr.add_check(r, [])
            return dr
        d.addCallback(_done)
        return d

    def deep_check_and_repair(self, verify=False, add_lease=False):
        d = self.check_and_repair(verify)
        def _done(r):
            dr = DeepCheckAndRepairResults(self.storage_index)
            dr.add_check(r, [])
            return dr
        d.addCallback(_done)
        return d

    def download_best_version(self, progress=None):
        return defer.succeed(self._download_best_version(progress=progress))


    def _download_best_version(self, ignored=None, progress=None):
        if isinstance(self.my_uri, uri.LiteralFileURI):
            return self.my_uri.data
        if self.storage_index not in self.all_contents:
            raise NotEnoughSharesError(None, 0, 3)
        return self.all_contents[self.storage_index]


    def overwrite(self, new_contents):
        assert not self.is_readonly()
        new_data = new_contents.read(new_contents.get_size())
        new_data = "".join(new_data)
        self.all_contents[self.storage_index] = new_data
        return defer.succeed(None)
    def modify(self, modifier):
        # this does not implement FileTooLargeError, but the real one does
        return defer.maybeDeferred(self._modify, modifier)
    def _modify(self, modifier):
        assert not self.is_readonly()
        old_contents = self.all_contents[self.storage_index]
        new_data = modifier(old_contents, None, True)
        self.all_contents[self.storage_index] = new_data
        return None

    # As actually implemented, MutableFilenode and MutableFileVersion
    # are distinct. However, nothing in the webapi uses (yet) that
    # distinction -- it just uses the unified download interface
    # provided by get_best_readable_version and read. When we start
    # doing cooler things like LDMF, we will want to revise this code to
    # be less simplistic.
    def get_best_readable_version(self):
        return defer.succeed(self)


    def get_best_mutable_version(self):
        return defer.succeed(self)

    # Ditto for this, which is an implementation of IWriteable.
    # XXX: Declare that the same is implemented.
    def update(self, data, offset):
        assert not self.is_readonly()
        def modifier(old, servermap, first_time):
            new = old[:offset] + "".join(data.read(data.get_size()))
            new += old[len(new):]
            return new
        return self.modify(modifier)


    def read(self, consumer, offset=0, size=None):
        data = self._download_best_version()
        if size:
            data = data[offset:offset+size]
        consumer.write(data)
        return defer.succeed(consumer)


def make_mutable_file_cap():
    return uri.WriteableSSKFileURI(writekey=os.urandom(16),
                                   fingerprint=os.urandom(32))

def make_mdmf_mutable_file_cap():
    return uri.WriteableMDMFFileURI(writekey=os.urandom(16),
                                   fingerprint=os.urandom(32))

def make_mutable_file_uri(mdmf=False):
    if mdmf:
        uri = make_mdmf_mutable_file_cap()
    else:
        uri = make_mutable_file_cap()

    return uri.to_string()

def make_verifier_uri():
    return uri.SSKVerifierURI(storage_index=os.urandom(16),
                              fingerprint=os.urandom(32)).to_string()

def create_mutable_filenode(contents, mdmf=False, all_contents=None):
    # XXX: All of these arguments are kind of stupid.
    if mdmf:
        cap = make_mdmf_mutable_file_cap()
    else:
        cap = make_mutable_file_cap()

    encoding_params = {}
    encoding_params['k'] = 3
    encoding_params['max_segment_size'] = 128*1024

    filenode = FakeMutableFileNode(None, None, encoding_params, None,
                                   all_contents)
    filenode.init_from_cap(cap)
    if mdmf:
        filenode.create(MutableData(contents), version=MDMF_VERSION)
    else:
        filenode.create(MutableData(contents), version=SDMF_VERSION)
    return filenode


class LoggingServiceParent(service.MultiService):
    def log(self, *args, **kwargs):
        return log.msg(*args, **kwargs)


TEST_DATA="\x02"*(Uploader.URI_LIT_SIZE_THRESHOLD+1)

class ShouldFailMixin:
    def shouldFail(self, expected_failure, which, substring,
                   callable, *args, **kwargs):
        """Assert that a function call raises some exception. This is a
        Deferred-friendly version of TestCase.assertRaises() .

        Suppose you want to verify the following function:

         def broken(a, b, c):
             if a < 0:
                 raise TypeError('a must not be negative')
             return defer.succeed(b+c)

        You can use:
            d = self.shouldFail(TypeError, 'test name',
                                'a must not be negative',
                                broken, -4, 5, c=12)
        in your test method. The 'test name' string will be included in the
        error message, if any, because Deferred chains frequently make it
        difficult to tell which assertion was tripped.

        The substring= argument, if not None, must appear in the 'repr'
        of the message wrapped by this Failure, or the test will fail.
        """

        assert substring is None or isinstance(substring, str)
        d = defer.maybeDeferred(callable, *args, **kwargs)
        def done(res):
            if isinstance(res, failure.Failure):
                res.trap(expected_failure)
                if substring:
                    message = repr(res.value.args[0])
                    self.failUnless(substring in message,
                                    "%s: substring '%s' not in '%s'"
                                    % (which, substring, message))
            else:
                self.fail("%s was supposed to raise %s, not get '%s'" %
                          (which, expected_failure, res))
        d.addBoth(done)
        return d

class WebErrorMixin:
    def explain_web_error(self, f):
        # an error on the server side causes the client-side getPage() to
        # return a failure(t.web.error.Error), and its str() doesn't show the
        # response body, which is where the useful information lives. Attach
        # this method as an errback handler, and it will reveal the hidden
        # message.
        f.trap(WebError)
        print "Web Error:", f.value, ":", f.value.response
        return f

    def _shouldHTTPError(self, res, which, validator):
        if isinstance(res, failure.Failure):
            res.trap(WebError)
            return validator(res)
        else:
            self.fail("%s was supposed to Error, not get '%s'" % (which, res))

    def shouldHTTPError(self, which,
                        code=None, substring=None, response_substring=None,
                        callable=None, *args, **kwargs):
        # returns a Deferred with the response body
        assert substring is None or isinstance(substring, str)
        assert callable
        def _validate(f):
            if code is not None:
                self.failUnlessEqual(f.value.status, str(code), which)
            if substring:
                code_string = str(f)
                self.failUnless(substring in code_string,
                                "%s: substring '%s' not in '%s'"
                                % (which, substring, code_string))
            response_body = f.value.response
            if response_substring:
                self.failUnless(response_substring in response_body,
                                "%s: response substring '%s' not in '%s'"
                                % (which, response_substring, response_body))
            return response_body
        d = defer.maybeDeferred(callable, *args, **kwargs)
        d.addBoth(self._shouldHTTPError, which, _validate)
        return d

class ErrorMixin(WebErrorMixin):
    def explain_error(self, f):
        if f.check(defer.FirstError):
            print "First Error:", f.value.subFailure
        return f

def corrupt_field(data, offset, size, debug=False):
    if random.random() < 0.5:
        newdata = testutil.flip_one_bit(data, offset, size)
        if debug:
            log.msg("testing: corrupting offset %d, size %d flipping one bit orig: %r, newdata: %r" % (offset, size, data[offset:offset+size], newdata[offset:offset+size]))
        return newdata
    else:
        newval = testutil.insecurerandstr(size)
        if debug:
            log.msg("testing: corrupting offset %d, size %d randomizing field, orig: %r, newval: %r" % (offset, size, data[offset:offset+size], newval))
        return data[:offset]+newval+data[offset+size:]

def _corrupt_nothing(data, debug=False):
    """Leave the data pristine. """
    return data

def _corrupt_file_version_number(data, debug=False):
    """Scramble the file data -- the share file version number have one bit
    flipped or else will be changed to a random value."""
    return corrupt_field(data, 0x00, 4)

def _corrupt_size_of_file_data(data, debug=False):
    """Scramble the file data -- the field showing the size of the share data
    within the file will be set to one smaller."""
    return corrupt_field(data, 0x04, 4)

def _corrupt_sharedata_version_number(data, debug=False):
    """Scramble the file data -- the share data version number will have one
    bit flipped or else will be changed to a random value, but not 1 or 2."""
    return corrupt_field(data, 0x0c, 4)
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    newsharevernum = sharevernum
    while newsharevernum in (1, 2):
        newsharevernum = random.randrange(0, 2**32)
    newsharevernumbytes = struct.pack(">L", newsharevernum)
    return data[:0x0c] + newsharevernumbytes + data[0x0c+4:]

def _corrupt_sharedata_version_number_to_plausible_version(data, debug=False):
    """Scramble the file data -- the share data version number will be
    changed to 2 if it is 1 or else to 1 if it is 2."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        newsharevernum = 2
    else:
        newsharevernum = 1
    newsharevernumbytes = struct.pack(">L", newsharevernum)
    return data[:0x0c] + newsharevernumbytes + data[0x0c+4:]

def _corrupt_segment_size(data, debug=False):
    """Scramble the file data -- the field showing the size of the segment
    will have one bit flipped or else be changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x04, 4, debug=False)
    else:
        return corrupt_field(data, 0x0c+0x04, 8, debug=False)

def _corrupt_size_of_sharedata(data, debug=False):
    """Scramble the file data -- the field showing the size of the data
    within the share data will have one bit flipped or else will be changed
    to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x08, 4)
    else:
        return corrupt_field(data, 0x0c+0x0c, 8)

def _corrupt_offset_of_sharedata(data, debug=False):
    """Scramble the file data -- the field showing the offset of the data
    within the share data will have one bit flipped or else be changed to a
    random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x0c, 4)
    else:
        return corrupt_field(data, 0x0c+0x14, 8)

def _corrupt_offset_of_ciphertext_hash_tree(data, debug=False):
    """Scramble the file data -- the field showing the offset of the
    ciphertext hash tree within the share data will have one bit flipped or
    else be changed to a random value.
    """
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x14, 4, debug=False)
    else:
        return corrupt_field(data, 0x0c+0x24, 8, debug=False)

def _corrupt_offset_of_block_hashes(data, debug=False):
    """Scramble the file data -- the field showing the offset of the block
    hash tree within the share data will have one bit flipped or else will be
    changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x18, 4)
    else:
        return corrupt_field(data, 0x0c+0x2c, 8)

def _corrupt_offset_of_block_hashes_to_truncate_crypttext_hashes(data, debug=False):
    """Scramble the file data -- the field showing the offset of the block
    hash tree within the share data will have a multiple of hash size
    subtracted from it, thus causing the downloader to download an incomplete
    crypttext hash tree."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        curval = struct.unpack(">L", data[0x0c+0x18:0x0c+0x18+4])[0]
        newval = random.randrange(0, max(1, (curval/hashutil.CRYPTO_VAL_SIZE)/2))*hashutil.CRYPTO_VAL_SIZE
        newvalstr = struct.pack(">L", newval)
        return data[:0x0c+0x18]+newvalstr+data[0x0c+0x18+4:]
    else:
        curval = struct.unpack(">Q", data[0x0c+0x2c:0x0c+0x2c+8])[0]
        newval = random.randrange(0, max(1, (curval/hashutil.CRYPTO_VAL_SIZE)/2))*hashutil.CRYPTO_VAL_SIZE
        newvalstr = struct.pack(">Q", newval)
        return data[:0x0c+0x2c]+newvalstr+data[0x0c+0x2c+8:]

def _corrupt_offset_of_share_hashes(data, debug=False):
    """Scramble the file data -- the field showing the offset of the share
    hash tree within the share data will have one bit flipped or else will be
    changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x1c, 4)
    else:
        return corrupt_field(data, 0x0c+0x34, 8)

def _corrupt_offset_of_uri_extension(data, debug=False):
    """Scramble the file data -- the field showing the offset of the uri
    extension will have one bit flipped or else will be changed to a random
    value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x20, 4)
    else:
        return corrupt_field(data, 0x0c+0x3c, 8)

def _corrupt_offset_of_uri_extension_to_force_short_read(data, debug=False):
    """Scramble the file data -- the field showing the offset of the uri
    extension will be set to the size of the file minus 3. This means when
    the client tries to read the length field from that location it will get
    a short read -- the result string will be only 3 bytes long, not the 4 or
    8 bytes necessary to do a successful struct.unpack."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    # The "-0x0c" in here is to skip the server-side header in the share
    # file, which the client doesn't see when seeking and reading.
    if sharevernum == 1:
        if debug:
            log.msg("testing: corrupting offset %d, size %d, changing %d to %d (len(data) == %d)" % (0x2c, 4, struct.unpack(">L", data[0x2c:0x2c+4])[0], len(data)-0x0c-3, len(data)))
        return data[:0x2c] + struct.pack(">L", len(data)-0x0c-3) + data[0x2c+4:]
    else:
        if debug:
            log.msg("testing: corrupting offset %d, size %d, changing %d to %d (len(data) == %d)" % (0x48, 8, struct.unpack(">Q", data[0x48:0x48+8])[0], len(data)-0x0c-3, len(data)))
        return data[:0x48] + struct.pack(">Q", len(data)-0x0c-3) + data[0x48+8:]

def _corrupt_mutable_share_data(data, debug=False):
    prefix = data[:32]
    assert prefix == MutableShareFile.MAGIC, "This function is designed to corrupt mutable shares of v1, and the magic number doesn't look right: %r vs %r" % (prefix, MutableShareFile.MAGIC)
    data_offset = MutableShareFile.DATA_OFFSET
    sharetype = data[data_offset:data_offset+1]
    assert sharetype == "\x00", "non-SDMF mutable shares not supported"
    (version, ig_seqnum, ig_roothash, ig_IV, ig_k, ig_N, ig_segsize,
     ig_datalen, offsets) = unpack_header(data[data_offset:])
    assert version == 0, "this function only handles v0 SDMF files"
    start = data_offset + offsets["share_data"]
    length = data_offset + offsets["enc_privkey"] - start
    return corrupt_field(data, start, length)

def _corrupt_share_data(data, debug=False):
    """Scramble the file data -- the field containing the share data itself
    will have one bit flipped or else will be changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways, not v%d." % sharevernum
    if sharevernum == 1:
        sharedatasize = struct.unpack(">L", data[0x0c+0x08:0x0c+0x08+4])[0]

        return corrupt_field(data, 0x0c+0x24, sharedatasize)
    else:
        sharedatasize = struct.unpack(">Q", data[0x0c+0x08:0x0c+0x0c+8])[0]

        return corrupt_field(data, 0x0c+0x44, sharedatasize)

def _corrupt_share_data_last_byte(data, debug=False):
    """Scramble the file data -- flip all bits of the last byte."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways, not v%d." % sharevernum
    if sharevernum == 1:
        sharedatasize = struct.unpack(">L", data[0x0c+0x08:0x0c+0x08+4])[0]
        offset = 0x0c+0x24+sharedatasize-1
    else:
        sharedatasize = struct.unpack(">Q", data[0x0c+0x08:0x0c+0x0c+8])[0]
        offset = 0x0c+0x44+sharedatasize-1

    newdata = data[:offset] + chr(ord(data[offset])^0xFF) + data[offset+1:]
    if debug:
        log.msg("testing: flipping all bits of byte at offset %d: %r, newdata: %r" % (offset, data[offset], newdata[offset]))
    return newdata

def _corrupt_crypttext_hash_tree(data, debug=False):
    """Scramble the file data -- the field containing the crypttext hash tree
    will have one bit flipped or else will be changed to a random value.
    """
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        crypttexthashtreeoffset = struct.unpack(">L", data[0x0c+0x14:0x0c+0x14+4])[0]
        blockhashesoffset = struct.unpack(">L", data[0x0c+0x18:0x0c+0x18+4])[0]
    else:
        crypttexthashtreeoffset = struct.unpack(">Q", data[0x0c+0x24:0x0c+0x24+8])[0]
        blockhashesoffset = struct.unpack(">Q", data[0x0c+0x2c:0x0c+0x2c+8])[0]

    return corrupt_field(data, 0x0c+crypttexthashtreeoffset, blockhashesoffset-crypttexthashtreeoffset, debug=debug)

def _corrupt_crypttext_hash_tree_byte_x221(data, debug=False):
    """Scramble the file data -- the byte at offset 0x221 will have its 7th
    (b1) bit flipped.
    """
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if debug:
        log.msg("original data: %r" % (data,))
    return data[:0x0c+0x221] + chr(ord(data[0x0c+0x221])^0x02) + data[0x0c+0x2210+1:]

def _corrupt_block_hashes(data, debug=False):
    """Scramble the file data -- the field containing the block hash tree
    will have one bit flipped or else will be changed to a random value.
    """
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        blockhashesoffset = struct.unpack(">L", data[0x0c+0x18:0x0c+0x18+4])[0]
        sharehashesoffset = struct.unpack(">L", data[0x0c+0x1c:0x0c+0x1c+4])[0]
    else:
        blockhashesoffset = struct.unpack(">Q", data[0x0c+0x2c:0x0c+0x2c+8])[0]
        sharehashesoffset = struct.unpack(">Q", data[0x0c+0x34:0x0c+0x34+8])[0]

    return corrupt_field(data, 0x0c+blockhashesoffset, sharehashesoffset-blockhashesoffset)

def _corrupt_share_hashes(data, debug=False):
    """Scramble the file data -- the field containing the share hash chain
    will have one bit flipped or else will be changed to a random value.
    """
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        sharehashesoffset = struct.unpack(">L", data[0x0c+0x1c:0x0c+0x1c+4])[0]
        uriextoffset = struct.unpack(">L", data[0x0c+0x20:0x0c+0x20+4])[0]
    else:
        sharehashesoffset = struct.unpack(">Q", data[0x0c+0x34:0x0c+0x34+8])[0]
        uriextoffset = struct.unpack(">Q", data[0x0c+0x3c:0x0c+0x3c+8])[0]

    return corrupt_field(data, 0x0c+sharehashesoffset, uriextoffset-sharehashesoffset)

def _corrupt_length_of_uri_extension(data, debug=False):
    """Scramble the file data -- the field showing the length of the uri
    extension will have one bit flipped or else will be changed to a random
    value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        uriextoffset = struct.unpack(">L", data[0x0c+0x20:0x0c+0x20+4])[0]
        return corrupt_field(data, uriextoffset, 4)
    else:
        uriextoffset = struct.unpack(">Q", data[0x0c+0x3c:0x0c+0x3c+8])[0]
        return corrupt_field(data, 0x0c+uriextoffset, 8)

def _corrupt_uri_extension(data, debug=False):
    """Scramble the file data -- the field containing the uri extension will
    have one bit flipped or else will be changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        uriextoffset = struct.unpack(">L", data[0x0c+0x20:0x0c+0x20+4])[0]
        uriextlen = struct.unpack(">L", data[0x0c+uriextoffset:0x0c+uriextoffset+4])[0]
    else:
        uriextoffset = struct.unpack(">Q", data[0x0c+0x3c:0x0c+0x3c+8])[0]
        uriextlen = struct.unpack(">Q", data[0x0c+uriextoffset:0x0c+uriextoffset+8])[0]

    return corrupt_field(data, 0x0c+uriextoffset, uriextlen)
