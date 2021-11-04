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

from zope.interface import (
    Interface,
    implementer,
)

from allmydata.util.hashutil import timing_safe_compare

# struct format for representation of a lease in an immutable share
IMMUTABLE_FORMAT = ">L32s32sL"

# struct format for representation of a lease in a mutable share
MUTABLE_FORMAT = ">LL32s32s20s"


class ILeaseInfo(Interface):
    """
    Represent a marker attached to a share that indicates that share should be
    retained for some amount of time.

    Typically clients will create and renew leases on their shares as a way to
    inform storage servers that there is still interest in those shares.  A
    share may have more than one lease.  If all leases on a share have
    expiration times in the past then the storage server may take this as a
    strong hint that no one is interested in the share anymore and therefore
    the share may be deleted to reclaim the space.
    """
    def renew(new_expire_time):
        """
        Create a new ``ILeaseInfo`` with the given expiration time.

        :param Union[int, float] new_expire_time: The expiration time the new
            ``ILeaseInfo`` will have.

        :return: The new ``ILeaseInfo`` provider with the new expiration time.
        """

    def get_expiration_time():
        """
        :return Union[int, float]: this lease's expiration time
        """

    def get_grant_renew_time_time():
        """
        :return Union[int, float]: a guess about the last time this lease was
            renewed
        """

    def get_age():
        """
        :return Union[int, float]: a guess about how long it has been since this
            lease was renewed
        """

    def to_immutable_data():
        """
        :return bytes: a serialized representation of this lease suitable for
            inclusion in an immutable container
        """

    def to_mutable_data():
        """
        :return bytes: a serialized representation of this lease suitable for
            inclusion in a mutable container
        """

    def immutable_size():
        """
        :return int: the size of the serialized representation of this lease in an
            immutable container
        """

    def mutable_size():
        """
        :return int: the size of the serialized representation of this lease in a
            mutable container
        """

    def is_renew_secret(candidate_secret):
        """
        :return bool: ``True`` if the given byte string is this lease's renew
            secret, ``False`` otherwise
        """

    def is_cancel_secret(candidate_secret):
        """
        :return bool: ``True`` if the given byte string is this lease's cancel
            secret, ``False`` otherwise
        """


@implementer(ILeaseInfo)
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

    def is_renew_secret(self, candidate_secret):
        # type: (bytes) -> bool
        """
        Check a string to see if it is the correct renew secret.

        :return: ``True`` if it is the correct renew secret, ``False``
            otherwise.
        """
        return timing_safe_compare(self.renew_secret, candidate_secret)

    def is_cancel_secret(self, candidate_secret):
        # type: (bytes) -> bool
        """
        Check a string to see if it is the correct cancel secret.

        :return: ``True`` if it is the correct cancel secret, ``False``
            otherwise.
        """
        return timing_safe_compare(self.cancel_secret, candidate_secret)

    def get_grant_renew_time_time(self):
        # hack, based upon fixed 31day expiration period
        return self._expiration_time - 31*24*60*60

    def get_age(self):
        return time.time() - self.get_grant_renew_time_time()

    @classmethod
    def from_immutable_data(cls, data):
        """
        Create a new instance from the encoded data given.

        :param data: A lease serialized using the immutable-share-file format.
        """
        names = [
            "owner_num",
            "renew_secret",
            "cancel_secret",
            "expiration_time",
        ]
        values = struct.unpack(">L32s32sL", data)
        return cls(nodeid=None, **dict(zip(names, values)))

    def immutable_size(self):
        """
        :return int: The size, in bytes, of the representation of this lease in an
            immutable share file.
        """
        return struct.calcsize(IMMUTABLE_FORMAT)

    def mutable_size(self):
        """
        :return int: The size, in bytes, of the representation of this lease in a
            mutable share file.
        """
        return struct.calcsize(MUTABLE_FORMAT)

    def to_immutable_data(self):
        return struct.pack(IMMUTABLE_FORMAT,
                           self.owner_num,
                           self.renew_secret, self.cancel_secret,
                           int(self._expiration_time))

    def to_mutable_data(self):
        return struct.pack(MUTABLE_FORMAT,
                           self.owner_num,
                           int(self._expiration_time),
                           self.renew_secret, self.cancel_secret,
                           self.nodeid)

    @classmethod
    def from_mutable_data(cls, data):
        """
        Create a new instance from the encoded data given.

        :param data: A lease serialized using the mutable-share-file format.
        """
        names = [
            "owner_num",
            "expiration_time",
            "renew_secret",
            "cancel_secret",
            "nodeid",
        ]
        values = struct.unpack(">LL32s32s20s", data)
        return cls(**dict(zip(names, values)))
