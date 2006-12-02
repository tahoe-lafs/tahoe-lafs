
from foolscap.schema import StringConstraint, ListOf, TupleOf, Any, Nothing
from foolscap import RemoteInterface

Nodeid = StringConstraint(20)
PBURL = StringConstraint()
# these three are here because Foolscap does not yet support the kind of
# restriction I really want to apply to these.
RIClient_ = Any
Referenceable_ = Any
RIBucketWriter_ = Any

class RIQueenRoster(RemoteInterface):
    def hello(nodeid=Nodeid, node=RIClient_, pburl=PBURL):
        return Nothing

class RIClient(RemoteInterface):
    def get_service(name=str):
        return Referenceable_
    def add_peers(new_peers=ListOf(TupleOf(Nodeid, PBURL), maxLength=100)):
        return Nothing
    def lost_peers(lost_peers=ListOf(Nodeid)):
        return Nothing

class RIStorageServer(RemoteInterface):
    def allocate_bucket(verifierid=Nodeid, bucket_num=int, size=int,
                        leaser=Nodeid):
        return RIBucketWriter_


class RIBucketWriter(RemoteInterface):
    def write(data=str):
        return Nothing

    def set_size(size=int):
        return Nothing

    def close():
        return Nothing


