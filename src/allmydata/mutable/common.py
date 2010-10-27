
from allmydata.util import idlib
from allmydata.util.spans import DataSpans

MODE_CHECK = "MODE_CHECK" # query all peers
MODE_ANYTHING = "MODE_ANYTHING" # one recoverable version
MODE_WRITE = "MODE_WRITE" # replace all shares, probably.. not for initial
                          # creation
MODE_READ = "MODE_READ"

class NotWriteableError(Exception):
    pass

class NeedMoreDataError(Exception):
    def __init__(self, needed_bytes, encprivkey_offset, encprivkey_length):
        Exception.__init__(self)
        self.needed_bytes = needed_bytes # up through EOF
        self.encprivkey_offset = encprivkey_offset
        self.encprivkey_length = encprivkey_length
    def __repr__(self):
        return "<NeedMoreDataError (%d bytes)>" % self.needed_bytes

class UncoordinatedWriteError(Exception):
    def __repr__(self):
        return ("<%s -- You, oh user, tried to change a file or directory "
                "at the same time as another process was trying to change it. "
                " To avoid data loss, don't do this.  Please see "
                "docs/write_coordination.html for details.>" %
                (self.__class__.__name__,))

class UnrecoverableFileError(Exception):
    pass

class NotEnoughServersError(Exception):
    """There were not enough functioning servers available to place shares
    upon. This might result from all servers being full or having an error, a
    local bug which causes all server requests to fail in the same way, or
    from there being zero servers. The first error received (if any) is
    stored in my .first_error attribute."""
    def __init__(self, why, first_error=None):
        Exception.__init__(self, why, first_error)
        self.first_error = first_error

class CorruptShareError(Exception):
    def __init__(self, peerid, shnum, reason):
        self.args = (peerid, shnum, reason)
        self.peerid = peerid
        self.shnum = shnum
        self.reason = reason
    def __str__(self):
        short_peerid = idlib.nodeid_b2a(self.peerid)[:8]
        return "<CorruptShareError peerid=%s shnum[%d]: %s" % (short_peerid,
                                                               self.shnum,
                                                               self.reason)

class UnknownVersionError(Exception):
    """The share we received was of a version we don't recognize."""

class ResponseCache:
    """I cache share data, to reduce the number of round trips used during
    mutable file operations. All of the data in my cache is for a single
    storage index, but I will keep information on multiple shares for
    that storage index.

    I maintain a highest-seen sequence number, and will flush all entries
    each time this number increases (this doesn't necessarily imply that
    all entries have the same sequence number).

    My cache is indexed by a (verinfo, shnum) tuple.

    My cache entries are DataSpans instances, each representing a set of
    non-overlapping byteranges.
    """

    def __init__(self):
        self.cache = {}
        self.seqnum = None

    def _clear(self):
        # also used by unit tests
        self.cache = {}

    def add(self, verinfo, shnum, offset, data):
        seqnum = verinfo[0]
        if seqnum > self.seqnum:
            self._clear()
            self.seqnum = seqnum

        index = (verinfo, shnum)
        if index in self.cache:
            self.cache[index].add(offset, data)
        else:
            spans = DataSpans()
            spans.add(offset, data)
            self.cache[index] = spans

    def read(self, verinfo, shnum, offset, length):
        """Try to satisfy a read request from cache.
        Returns data, or None if the cache did not hold the entire requested span.
        """

        # TODO: perhaps return a DataSpans object representing the fragments
        # that we have, instead of only returning a hit if we can satisfy the
        # whole request from cache.

        index = (verinfo, shnum)
        if index in self.cache:
            return self.cache[index].get(offset, length)
        else:
            return None
