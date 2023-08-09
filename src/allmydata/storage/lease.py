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

from twisted.python.components import (
    proxyForInterface,
)

from allmydata.util.hashutil import timing_safe_compare
from allmydata.util import base32

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

    def present_renew_secret():
        """
        :return str: Text which could reasonably be shown to a person representing
            this lease's renew secret.
        """

    def is_cancel_secret(candidate_secret):
        """
        :return bool: ``True`` if the given byte string is this lease's cancel
            secret, ``False`` otherwise
        """

    def present_cancel_secret():
        """
        :return str: Text which could reasonably be shown to a person representing
            this lease's cancel secret.
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
            # MyPy is unhappy with this; long-term solution is likely switch to
            # new @frozen attrs API, with type annotations.
            _expiration_time=new_expire_time,  # type: ignore[call-arg]
        )

    def is_renew_secret(self, candidate_secret):
        # type: (bytes) -> bool
        """
        Check a string to see if it is the correct renew secret.

        :return: ``True`` if it is the correct renew secret, ``False``
            otherwise.
        """
        return timing_safe_compare(self.renew_secret, candidate_secret)

    def present_renew_secret(self):
        # type: () -> str
        """
        Return the renew secret, base32-encoded.
        """
        return str(base32.b2a(self.renew_secret), "utf-8")

    def is_cancel_secret(self, candidate_secret):
        # type: (bytes) -> bool
        """
        Check a string to see if it is the correct cancel secret.

        :return: ``True`` if it is the correct cancel secret, ``False``
            otherwise.
        """
        return timing_safe_compare(self.cancel_secret, candidate_secret)

    def present_cancel_secret(self):
        # type: () -> str
        """
        Return the cancel secret, base32-encoded.
        """
        return str(base32.b2a(self.cancel_secret), "utf-8")

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
        values = struct.unpack(IMMUTABLE_FORMAT, data)
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
        values = struct.unpack(MUTABLE_FORMAT, data)
        return cls(**dict(zip(names, values)))


@attr.s(frozen=True)
class HashedLeaseInfo(proxyForInterface(ILeaseInfo, "_lease_info")): # type: ignore # unsupported dynamic base class
    """
    A ``HashedLeaseInfo`` wraps lease information in which the secrets have
    been hashed.
    """
    _lease_info = attr.ib()
    _hash = attr.ib()

    # proxyForInterface will take care of forwarding all methods on ILeaseInfo
    # to `_lease_info`.  Here we override a few of those methods to adjust
    # their behavior to make them suitable for use with hashed secrets.

    def renew(self, new_expire_time):
        # Preserve the HashedLeaseInfo wrapper around the renewed LeaseInfo.
        return attr.assoc(
            self,
            _lease_info=super(HashedLeaseInfo, self).renew(new_expire_time),
        )

    def is_renew_secret(self, candidate_secret):
        # type: (bytes) -> bool
        """
        Hash the candidate secret and compare the result to the stored hashed
        secret.
        """
        return super(HashedLeaseInfo, self).is_renew_secret(self._hash(candidate_secret))

    def present_renew_secret(self):
        # type: () -> str
        """
        Present the hash of the secret with a marker indicating it is a hash.
        """
        return u"hash:" + super(HashedLeaseInfo, self).present_renew_secret()

    def is_cancel_secret(self, candidate_secret):
        # type: (bytes) -> bool
        """
        Hash the candidate secret and compare the result to the stored hashed
        secret.
        """
        if isinstance(candidate_secret, _HashedCancelSecret):
            # Someone read it off of this object in this project - probably
            # the lease crawler - and is just trying to use it to identify
            # which lease it wants to operate on.  Avoid re-hashing the value.
            #
            # It is important that this codepath is only availably internally
            # for this process to talk to itself.  If it were to be exposed to
            # clients over the network, they could just provide the hashed
            # value to avoid having to ever learn the original value.
            hashed_candidate = candidate_secret.hashed_value
        else:
            # It is not yet hashed so hash it.
            hashed_candidate = self._hash(candidate_secret)

        return super(HashedLeaseInfo, self).is_cancel_secret(hashed_candidate)

    def present_cancel_secret(self):
        # type: () -> str
        """
        Present the hash of the secret with a marker indicating it is a hash.
        """
        return u"hash:" + super(HashedLeaseInfo, self).present_cancel_secret()

    @property
    def owner_num(self):
        return self._lease_info.owner_num

    @property
    def nodeid(self):
        return self._lease_info.nodeid

    @property
    def cancel_secret(self):
        """
        Give back an opaque wrapper around the hashed cancel secret which can
        later be presented for a succesful equality comparison.
        """
        # We don't *have* the cancel secret.  We hashed it and threw away the
        # original.  That's good.  It does mean that some code that runs
        # in-process with the storage service (LeaseCheckingCrawler) runs into
        # some difficulty.  That code wants to cancel leases and does so using
        # the same interface that faces storage clients (or would face them,
        # if lease cancellation were exposed).
        #
        # Since it can't use the hashed secret to cancel a lease (that's the
        # point of the hashing) and we don't have the unhashed secret to give
        # it, instead we give it a marker that `cancel_lease` will recognize.
        # On recognizing it, if the hashed value given matches the hashed
        # value stored it is considered a match and the lease can be
        # cancelled.
        #
        # This isn't great.  Maybe the internal and external consumers of
        # cancellation should use different interfaces.
        return _HashedCancelSecret(self._lease_info.cancel_secret)


@attr.s(frozen=True)
class _HashedCancelSecret(object):
    """
    ``_HashedCancelSecret`` is a marker type for an already-hashed lease
    cancel secret that lets internal lease cancellers bypass the hash-based
    protection that's imposed on external lease cancellers.

    :ivar bytes hashed_value: The already-hashed secret.
    """
    hashed_value = attr.ib()
