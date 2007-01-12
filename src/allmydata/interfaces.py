
from zope.interface import Interface
from foolscap.schema import StringConstraint, ListOf, TupleOf, Any, Nothing
from foolscap import RemoteInterface

Nodeid = StringConstraint(20) # binary format 20-byte SHA1 hash
PBURL = StringConstraint(150)
Verifierid = StringConstraint(20)
ShareData = StringConstraint(100000)
# these four are here because Foolscap does not yet support the kind of
# restriction I really want to apply to these.
RIClient_ = Any()
Referenceable_ = Any()
RIBucketWriter_ = Any()
RIBucketReader_ = Any()
RIMutableDirectoryNode_ = Any()
RIMutableFileNode_ = Any()

class RIQueenRoster(RemoteInterface):
    def hello(nodeid=Nodeid, node=RIClient_, pburl=PBURL):
        return RIMutableDirectoryNode_ # the virtual drive root

class RIClient(RemoteInterface):
    def get_service(name=str):
        return Referenceable_
    def add_peers(new_peers=ListOf(TupleOf(Nodeid, PBURL), maxLength=100)):
        return Nothing()
    def lost_peers(lost_peers=ListOf(Nodeid)):
        return Nothing()

class RIStorageServer(RemoteInterface):
    def allocate_bucket(verifierid=Verifierid, bucket_num=int, size=int,
                        leaser=Nodeid, canary=Referenceable_):
        # if the canary is lost before close(), the bucket is deleted
        return RIBucketWriter_
    def get_buckets(verifierid=Verifierid):
        return ListOf(TupleOf(int, RIBucketReader_))

class RIBucketWriter(RemoteInterface):
    def write(data=ShareData):
        return Nothing()

    def close():
        return Nothing()


class RIBucketReader(RemoteInterface):
    def read():
        return ShareData


class RIMutableDirectoryNode(RemoteInterface):
    def list():
        return ListOf( TupleOf(str, # name, relative to directory
                               (RIMutableDirectoryNode_, Verifierid)),
                       maxLength=100,
                       )

    def get(name=str):
        return (RIMutableDirectoryNode_, Verifierid)

    def add_directory(name=str):
        return RIMutableDirectoryNode_

    def add_file(name=str, data=Verifierid):
        return Nothing()

    def remove(name=str):
        return Nothing()

    # need more to move directories


class ICodecEncoder(Interface):
    def set_params(data_size, required_shares, total_shares):
        """Set up the parameters of this encoder.

        See encode() for a description of how these parameters are used.
        """

    def get_encoder_type():
        """Return an integer that describes the type of this encoder.

        There must be a global table of encoder classes. This method returns
        an index into this table; the value at this index is an encoder
        class, and this encoder is an instance of that class.
        """

    def get_serialized_params(): # TODO: maybe, maybe not
        """Return a string that describes the parameters of this encoder.

        This string can be passed to the decoder to prepare it for handling
        the encoded shares we create. It might contain more information than
        was presented to set_params(), if there is some flexibility of
        parameter choice.

        This string is intended to be embedded in the URI, so there are
        several restrictions on its contents. At the moment I'm thinking that
        this means it may contain hex digits and colons, and nothing else.
        The idea is that the URI contains '%d:%s.' %
        (encoder.get_encoder_type(), encoder.get_serialized_params()), and
        this is enough information to construct a compatible decoder.
        """

    def get_share_size():
        """Return the length of the shares that encode() will produce.
        """

    def encode(data):
        """Encode a chunk of data. This may be called multiple times. Each
        call is independent.

        The data must be a string with a length that exactly matches the
        data_size promised by set_params().

        For each call, encode() will return a Deferred that fires with a list
        of 'total_shares' tuples. Each tuple is of the form (sharenum,
        share), where sharenum is an int (from 0 total_shares-1), and share
        is a string. The get_share_size() method can be used to determine the
        length of the 'share' strings returned by encode().

        The memory usage of this function is expected to be on the order of
        total_shares * get_share_size().
        """

class ICodecDecoder(Interface):
    def set_serialized_params(params):
        """Set up the parameters of this encoder, from a string returned by
        encoder.get_serialized_params()."""

    def decode(some_shares):
        """Decode a partial list of shares into data.

        'some_shares' must be a list of (sharenum, share) tuples, a subset of
        the shares returned by ICodecEncode.encode(). Each share must be of
        the same length. The share tuples may appear in any order, but of
        course each tuple must have a sharenum that correctly matches the
        associated share data string.

        This returns a Deferred which fires with a string. This string will
        always have a length equal to the 'data_size' value passed into the
        original ICodecEncode.set_params() call.

        The length of 'some_shares' must be equal or greater than the value
        of 'required_shares' passed into the original
        ICodecEncode.set_params() call.
        """
