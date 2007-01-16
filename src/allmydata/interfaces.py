
from zope.interface import Interface
from foolscap.schema import StringConstraint, ListOf, TupleOf, Any
from foolscap import RemoteInterface

Nodeid = StringConstraint(20) # binary format 20-byte SHA1 hash
PBURL = StringConstraint(150)
Verifierid = StringConstraint(20)
URI = StringConstraint(100) # kind of arbitrary
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
        return None
    def lost_peers(lost_peers=ListOf(Nodeid)):
        return None

class RIStorageServer(RemoteInterface):
    def allocate_bucket(verifierid=Verifierid, bucket_num=int, size=int,
                        leaser=Nodeid, canary=Referenceable_):
        # if the canary is lost before close(), the bucket is deleted
        return RIBucketWriter_
    def get_buckets(verifierid=Verifierid):
        return ListOf(TupleOf(int, RIBucketReader_))

class RIBucketWriter(RemoteInterface):
    def write(data=ShareData):
        return None
    def set_metadata(metadata=str):
        return None
    def close():
        return None


class RIBucketReader(RemoteInterface):
    def read():
        return ShareData
    def get_metadata():
        return str


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

    def add_file(name=str, uri=URI):
        return None

    def remove(name=str):
        return None

    # need more to move directories


class ICodecEncoder(Interface):
    def set_params(data_size, required_shares, max_shares):
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

    def encode(data, num_shares=None):
        """Encode a chunk of data. This may be called multiple times. Each
        call is independent.

        The data must be a string with a length that exactly matches the
        data_size promised by set_params().

        'num_shares', if provided, must be equal or less than the
        'max_shares' set in set_params. If 'num_shares' is left at None, this
        method will produce 'max_shares' shares. This can be used to minimize
        the work that the encoder needs to do if we initially thought that we
        would need, say, 100 shares, but now that it is time to actually
        encode the data we only have 75 peers to send data to.

        For each call, encode() will return a Deferred that fires with a list
        of 'total_shares' tuples. Each tuple is of the form (sharenum,
        sharedata), where sharenum is an int (from 0 total_shares-1), and
        sharedata is a string. The get_share_size() method can be used to
        determine the length of the 'sharedata' strings returned by encode().

        The (sharenum, sharedata) tuple must be kept together during storage
        and retrieval. Specifically, the share data is useless by itself: the
        decoder needs to be told which share is which by providing it with
        both the share number and the actual share data.

        The memory usage of this function is expected to be on the order of
        total_shares * get_share_size().
        """
        # design note: we could embed the share number in the sharedata by
        # returning bencode((sharenum,sharedata)). The advantage would be
        # making it easier to keep these two pieces together, and probably
        # avoiding a round trip when reading the remote bucket (although this
        # could be achieved by changing RIBucketReader.read to
        # read_data_and_metadata). The disadvantage is that the share number
        # wants to be exposed to the storage/bucket layer (specifically to
        # handle the next stage of peer-selection algorithm in which we
        # propose to keep share#A on a given peer and they are allowed to
        # tell us that they already have share#B). Also doing this would make
        # the share size somewhat variable (one-digit sharenumbers will be a
        # byte shorter than two-digit sharenumbers), unless we zero-pad the
        # sharenumbers based upon the max_total_shares declared in
        # set_params.

class ICodecDecoder(Interface):
    def set_serialized_params(params):
        """Set up the parameters of this encoder, from a string returned by
        encoder.get_serialized_params()."""

    def get_required_shares():
        """Return the number of shares needed to reconstruct the data.
        set_serialized_params() must be called before this."""

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
