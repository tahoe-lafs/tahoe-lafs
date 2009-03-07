import struct, time

class LeaseInfo:
    def __init__(self, owner_num=None, renew_secret=None, cancel_secret=None,
                 expiration_time=None, nodeid=None):
        self.owner_num = owner_num
        self.renew_secret = renew_secret
        self.cancel_secret = cancel_secret
        self.expiration_time = expiration_time
        if nodeid is not None:
            assert isinstance(nodeid, str)
            assert len(nodeid) == 20
        self.nodeid = nodeid

    def get_expiration_time(self):
        return self.expiration_time
    def get_grant_renew_time_time(self):
        # hack, based upon fixed 31day expiration period
        return self.expiration_time - 31*24*60*60
    def get_age(self):
        return time.time() - self.get_grant_renew_time_time()

    def from_immutable_data(self, data):
        (self.owner_num,
         self.renew_secret,
         self.cancel_secret,
         self.expiration_time) = struct.unpack(">L32s32sL", data)
        self.nodeid = None
        return self
    def to_immutable_data(self):
        return struct.pack(">L32s32sL",
                           self.owner_num,
                           self.renew_secret, self.cancel_secret,
                           int(self.expiration_time))

    def to_mutable_data(self):
        return struct.pack(">LL32s32s20s",
                           self.owner_num,
                           int(self.expiration_time),
                           self.renew_secret, self.cancel_secret,
                           self.nodeid)
    def from_mutable_data(self, data):
        (self.owner_num,
         self.expiration_time,
         self.renew_secret, self.cancel_secret,
         self.nodeid) = struct.unpack(">LL32s32s20s", data)
        return self
