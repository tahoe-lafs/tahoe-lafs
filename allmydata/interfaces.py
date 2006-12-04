
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
