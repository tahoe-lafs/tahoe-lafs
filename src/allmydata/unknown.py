
from zope.interface import implements
from twisted.internet import defer
from allmydata.interfaces import IFilesystemNode, MustNotBeUnknownRWError, \
    MustBeDeepImmutableError
from allmydata import uri
from allmydata.uri import ALLEGED_READONLY_PREFIX, ALLEGED_IMMUTABLE_PREFIX


# See ticket #833 for design rationale of UnknownNodes.

def strip_prefix_for_ro(ro_uri, deep_immutable):
    """Strip prefixes when storing an URI in a ro_uri slot."""

    # It is possible for an alleged-immutable URI to be put into a
    # mutable directory. In that case the ALLEGED_IMMUTABLE_PREFIX
    # should not be stripped. In other cases, the prefix can safely
    # be stripped because it is implied by the context.

    if ro_uri.startswith(ALLEGED_IMMUTABLE_PREFIX):
        if not deep_immutable:
            return ro_uri
        return ro_uri[len(ALLEGED_IMMUTABLE_PREFIX):]
    elif ro_uri.startswith(ALLEGED_READONLY_PREFIX):
        return ro_uri[len(ALLEGED_READONLY_PREFIX):]
    else:
        return ro_uri

class UnknownNode:
    implements(IFilesystemNode)

    def __init__(self, given_rw_uri, given_ro_uri, deep_immutable=False,
                 name=u"<unknown name>"):
        assert given_rw_uri is None or isinstance(given_rw_uri, str)
        assert given_ro_uri is None or isinstance(given_ro_uri, str)
        given_rw_uri = given_rw_uri or None
        given_ro_uri = given_ro_uri or None

        # We don't raise errors when creating an UnknownNode; we instead create an
        # opaque node (with rw_uri and ro_uri both None) that records the error.
        # This avoids breaking operations that never store the opaque node.
        # Note that this means that if a stored dirnode has only a rw_uri, it
        # might be dropped. Any future "write-only" cap formats should have a dummy
        # unusable readcap to stop that from happening.

        self.error = None
        self.rw_uri = self.ro_uri = None
        if given_rw_uri:
            if deep_immutable:
                if given_rw_uri.startswith(ALLEGED_IMMUTABLE_PREFIX) and not given_ro_uri:
                    # We needed an immutable cap, and were given one. It was given in the
                    # rw_uri slot, but that's fine; we'll move it to ro_uri below.
                    pass
                elif not given_ro_uri:
                    self.error = MustNotBeUnknownRWError("cannot attach unknown rw cap as immutable child",
                                                         name, True)
                    return  # node will be opaque
                else:
                    # We could report either error, but this probably makes more sense.
                    self.error = MustBeDeepImmutableError("cannot attach unknown rw cap as immutable child",
                                                         name)
                    return  # node will be opaque

            if not given_ro_uri:
                # We were given a single cap argument, or a rw_uri with no ro_uri.

                if not (given_rw_uri.startswith(ALLEGED_READONLY_PREFIX)
                        or given_rw_uri.startswith(ALLEGED_IMMUTABLE_PREFIX)):
                    # If the single cap is unprefixed, then we cannot tell whether it is a
                    # writecap, and we don't know how to diminish it to a readcap if it is one.
                    # If it didn't *already* have at least an ALLEGED_READONLY_PREFIX, then
                    # prefixing it would be a bad idea because we have been given no reason
                    # to believe that it is a readcap, so we might be letting a client
                    # inadvertently grant excess write authority.
                    self.error = MustNotBeUnknownRWError("cannot attach unknown rw cap as child",
                                                         name, False)
                    return  # node will be opaque

                # OTOH, if the single cap already had a prefix (which is of the required
                # strength otherwise an error would have been thrown above), then treat it
                # as though it had been given in the ro_uri slot. This has a similar effect
                # to the use for known caps of 'bigcap = writecap or readcap' in
                # nodemaker.py: create_from_cap. It enables copying of unknown readcaps to
                # work in as many cases as we can securely allow.
                given_ro_uri = given_rw_uri
                given_rw_uri = None
            elif given_ro_uri.startswith(ALLEGED_IMMUTABLE_PREFIX):
                # Strange corner case: we were given a cap in both slots, with the ro_uri
                # alleged to be immutable. A real immutable object wouldn't have a writecap.
                self.error = MustBeDeepImmutableError("cannot accept a child entry that specifies "
                                                      "both rw_uri, and ro_uri with an imm. prefix",
                                                      name)
                return  # node will be opaque

        # If the ro_uri definitely fails the constraint, it should be treated as opaque and
        # the error recorded.
        if given_ro_uri:
            read_cap = uri.from_string(given_ro_uri, deep_immutable=deep_immutable, name=name)
            if isinstance(read_cap, uri.UnknownURI):
                self.error = read_cap.get_error()
                if self.error:
                    assert self.rw_uri is None and self.ro_uri is None
                    return

        if deep_immutable:
            assert self.rw_uri is None
            # strengthen the constraint on ro_uri to ALLEGED_IMMUTABLE_PREFIX
            if given_ro_uri:
                if given_ro_uri.startswith(ALLEGED_IMMUTABLE_PREFIX):
                    self.ro_uri = given_ro_uri
                elif given_ro_uri.startswith(ALLEGED_READONLY_PREFIX):
                    self.ro_uri = ALLEGED_IMMUTABLE_PREFIX + given_ro_uri[len(ALLEGED_READONLY_PREFIX):]
                else:
                    self.ro_uri = ALLEGED_IMMUTABLE_PREFIX + given_ro_uri
        else:
            # not immutable, so a writecap is allowed
            self.rw_uri = given_rw_uri
            # strengthen the constraint on ro_uri to ALLEGED_READONLY_PREFIX
            if given_ro_uri:
                if (given_ro_uri.startswith(ALLEGED_READONLY_PREFIX) or
                    given_ro_uri.startswith(ALLEGED_IMMUTABLE_PREFIX)):
                    self.ro_uri = given_ro_uri
                else:
                    self.ro_uri = ALLEGED_READONLY_PREFIX + given_ro_uri

    def get_cap(self):
        return uri.UnknownURI(self.rw_uri or self.ro_uri)

    def get_readcap(self):
        return uri.UnknownURI(self.ro_uri)

    def is_readonly(self):
        raise AssertionError("an UnknownNode might be either read-only or "
                             "read/write, so we shouldn't be calling is_readonly")

    def is_mutable(self):
        raise AssertionError("an UnknownNode might be either mutable or immutable, "
                             "so we shouldn't be calling is_mutable")

    def is_unknown(self):
        return True

    def is_allowed_in_immutable_directory(self):
        # An UnknownNode consisting only of a ro_uri is allowed in an
        # immutable directory, even though we do not know that it is
        # immutable (or even read-only), provided that no error was detected.
        return not self.error and not self.rw_uri

    def raise_error(self):
        if self.error is not None:
            raise self.error

    def get_uri(self):
        return self.rw_uri or self.ro_uri

    def get_write_uri(self):
        return self.rw_uri

    def get_readonly_uri(self):
        return self.ro_uri

    def get_storage_index(self):
        return None

    def get_verify_cap(self):
        return None

    def get_repair_cap(self):
        return None

    def get_size(self):
        return None

    def get_current_size(self):
        return defer.succeed(None)

    def check(self, monitor, verify, add_lease):
        return defer.succeed(None)

    def check_and_repair(self, monitor, verify, add_lease):
        return defer.succeed(None)
