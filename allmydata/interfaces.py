
from foolscap.schema import StringConstraint, ListOf, TupleOf, Any, Nothing
from foolscap import RemoteInterface

Nodeid = StringConstraint(20) # base32 encoded 20-byte SHA1 hash
PBURL = StringConstraint()
Tubid = StringConstraint()
ShareData = StringConstraint(20000)
# these three are here because Foolscap does not yet support the kind of
# restriction I really want to apply to these.
RIClient_ = Any()
Referenceable_ = Any()
RIBucketWriter_ = Any()

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
    def allocate_bucket(verifierid=Nodeid, bucket_num=int, size=int,
                        leaser=Tubid):
        return RIBucketWriter_


class RIBucketWriter(RemoteInterface):
    def write(data=ShareData):
        return Nothing()

    def set_size(size=int):
        return Nothing()

    def close():
        return Nothing()


