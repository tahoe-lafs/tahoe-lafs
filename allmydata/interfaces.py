
from foolscap.schema import StringConstraint, ListOf, TupleOf, Any, Nothing
from foolscap import RemoteInterface

Nodeid = StringConstraint(20) # binary format 20-byte SHA1 hash
PBURL = StringConstraint(150)
Verifierid = StringConstraint(20)
ShareData = StringConstraint(20000)
# these four are here because Foolscap does not yet support the kind of
# restriction I really want to apply to these.
RIClient_ = Any()
Referenceable_ = Any()
RIBucketWriter_ = Any()
RIBucketReader_ = Any()

class RIQueenRoster(RemoteInterface):
    def hello(nodeid=Nodeid, node=RIClient_, pburl=PBURL):
        return Nothing()

class RIClient(RemoteInterface):
    def get_service(name=str):
        return Referenceable_
    def add_peers(new_peers=ListOf(TupleOf(Nodeid, PBURL), maxLength=100)):
        return Nothing()
    def lost_peers(lost_peers=ListOf(Nodeid)):
        return Nothing()

class RIStorageServer(RemoteInterface):
    def allocate_bucket(verifierid=Verifierid, bucket_num=int, size=int,
                        leaser=Nodeid):
        return RIBucketWriter_
    def get_bucket(verifierid=Verifierid):
        return RIBucketReader_

class RIBucketWriter(RemoteInterface):
    def write(data=ShareData):
        return Nothing()

    def set_size(size=int):
        return Nothing()

    def close():
        return Nothing()


class RIBucketReader(RemoteInterface):
    def get_bucket_num():
        return int

    def read():
        return ShareData


