"""
Ported to Python 3.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import struct, time

import attr

@attr.s(frozen=True)
class LeaseInfo(object):
    """
    Represent the details of one lease, a marker which is intended to inform
    the storage server how long to store a particular share.
    """
    owner_num = attr.ib(default=None)

    # Don't put secrets into the default string representation.  This makes it
    # slightly less likely the secrets will accidentally be leaked to
    # someplace they're not meant to be.
    renew_secret = attr.ib(default=None, repr=False)
    cancel_secret = attr.ib(default=None, repr=False)

    _expiration_time = attr.ib(default=None)

    nodeid = attr.ib(default=None)

    @nodeid.validator
    def _validate_nodeid(self, attribute, value):
        if value is not None:
            if not isinstance(value, bytes):
                raise ValueError(
                    "nodeid value must be bytes, not {!r}".format(value),
                )
            if len(value) != 20:
                raise ValueError(
                    "nodeid value must be 20 bytes long, not {!r}".format(value),
                )
        return None

    def get_expiration_time(self):
        # type: () -> float
        """
        Retrieve a POSIX timestamp representing the time at which this lease is
        set to expire.
        """
        return self._expiration_time

    def renew(self, new_expire_time):
        # type: (float) -> LeaseInfo
        """
        Create a new lease the same as this one but with a new expiration time.

        :param new_expire_time: The new expiration time.

        :return: The new lease info.
        """
        return attr.assoc(
            self,
            _expiration_time=new_expire_time,
        )

    def get_grant_renew_time_time(self):
        # hack, based upon fixed 31day expiration period
        return self._expiration_time - 31*24*60*60

    def get_age(self):
        return time.time() - self.get_grant_renew_time_time()

    def from_immutable_data(self, data):
        (self.owner_num,
         self.renew_secret,
         self.cancel_secret,
         self._expiration_time) = struct.unpack(">L32s32sL", data)
        self.nodeid = None
        return self

    def to_immutable_data(self):
        return struct.pack(">L32s32sL",
                           self.owner_num,
                           self.renew_secret, self.cancel_secret,
                           int(self._expiration_time))

    def to_mutable_data(self):
        return struct.pack(">LL32s32s20s",
                           self.owner_num,
                           int(self._expiration_time),
                           self.renew_secret, self.cancel_secret,
                           self.nodeid)

    def from_mutable_data(self, data):
        (self.owner_num,
         self._expiration_time,
         self.renew_secret, self.cancel_secret,
         self.nodeid) = struct.unpack(">LL32s32s20s", data)
        return self
