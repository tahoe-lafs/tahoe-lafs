
from allmydata.util import idlib

MODE_CHECK = "query all peers"
MODE_ANYTHING = "one recoverable version"
MODE_WRITE = "replace all shares, probably" # not for initial creation
MODE_ENOUGH = "enough"

class NotMutableError(Exception):
    pass

class NeedMoreDataError(Exception):
    def __init__(self, needed_bytes, encprivkey_offset, encprivkey_length):
        Exception.__init__(self)
        self.needed_bytes = needed_bytes # up through EOF
        self.encprivkey_offset = encprivkey_offset
        self.encprivkey_length = encprivkey_length
    def __str__(self):
        return "<NeedMoreDataError (%d bytes)>" % self.needed_bytes

class UncoordinatedWriteError(Exception):
    def __repr__(self):
        return "<%s -- You, oh user, tried to change a file or directory at the same time as another process was trying to change it.  To avoid data loss, don't do this.  Please see docs/write_coordination.html for details.>" % (self.__class__.__name__,)

class UnrecoverableFileError(Exception):
    pass

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





class DictOfSets(dict):
    def add(self, key, value):
        if key in self:
            self[key].add(value)
        else:
            self[key] = set([value])

    def discard(self, key, value):
        if not key in self:
            return
        self[key].discard(value)
        if not self[key]:
            del self[key]

class ResponseCache:
    """I cache share data, to reduce the number of round trips used during
    mutable file operations. All of the data in my cache is for a single
    storage index, but I will keep information on multiple shares (and
    multiple versions) for that storage index.

    My cache is indexed by a (verinfo, shnum) tuple.

    My cache entries contain a set of non-overlapping byteranges: (start,
    data, timestamp) tuples.
    """

    def __init__(self):
        self.cache = DictOfSets()

    def _does_overlap(self, x_start, x_length, y_start, y_length):
        if x_start < y_start:
            x_start, y_start = y_start, x_start
            x_length, y_length = y_length, x_length
        x_end = x_start + x_length
        y_end = y_start + y_length
        # this just returns a boolean. Eventually we'll want a form that
        # returns a range.
        if not x_length:
            return False
        if not y_length:
            return False
        if x_start >= y_end:
            return False
        if y_start >= x_end:
            return False
        return True


    def _inside(self, x_start, x_length, y_start, y_length):
        x_end = x_start + x_length
        y_end = y_start + y_length
        if x_start < y_start:
            return False
        if x_start >= y_end:
            return False
        if x_end < y_start:
            return False
        if x_end > y_end:
            return False
        return True

    def add(self, verinfo, shnum, offset, data, timestamp):
        index = (verinfo, shnum)
        self.cache.add(index, (offset, data, timestamp) )

    def read(self, verinfo, shnum, offset, length):
        """Try to satisfy a read request from cache.
        Returns (data, timestamp), or (None, None) if the cache did not hold
        the requested data.
        """

        # TODO: join multiple fragments, instead of only returning a hit if
        # we have a fragment that contains the whole request

        index = (verinfo, shnum)
        end = offset+length
        for entry in self.cache.get(index, set()):
            (e_start, e_data, e_timestamp) = entry
            if self._inside(offset, length, e_start, len(e_data)):
                want_start = offset - e_start
                want_end = offset+length - e_start
                return (e_data[want_start:want_end], e_timestamp)
        return None, None


