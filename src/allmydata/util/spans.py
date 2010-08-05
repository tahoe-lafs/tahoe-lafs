
class Spans:
    """I represent a compressed list of booleans, one per index (an integer).
    Typically, each index represents an offset into a large string, pointing
    to a specific byte of a share. In this context, True means that byte has
    been received, or has been requested.

    Another way to look at this is maintaining a set of integers, optimized
    for operations on spans like 'add range to set' and 'is range in set?'.

    This is a python equivalent of perl's Set::IntSpan module, frequently
    used to represent .newsrc contents.

    Rather than storing an actual (large) list or dictionary, I represent my
    internal state as a sorted list of spans, each with a start and a length.
    My API is presented in terms of start+length pairs. I provide set
    arithmetic operators, to efficiently answer questions like 'I want bytes
    XYZ, I already requested bytes ABC, and I've already received bytes DEF:
    what bytes should I request now?'.

    The new downloader will use it to keep track of which bytes we've requested
    or received already.
    """

    def __init__(self, _span_or_start=None, length=None):
        self._spans = list()
        if length is not None:
            self._spans.append( (_span_or_start, length) )
        elif _span_or_start:
            for (start,length) in _span_or_start:
                self.add(start, length)
        self._check()

    def _check(self):
        assert sorted(self._spans) == self._spans
        prev_end = None
        try:
            for (start,length) in self._spans:
                if prev_end is not None:
                    assert start > prev_end
                prev_end = start+length
        except AssertionError:
            print "BAD:", self.dump()
            raise

    def add(self, start, length):
        assert start >= 0
        assert length > 0
        #print " ADD [%d+%d -%d) to %s" % (start, length, start+length, self.dump())
        first_overlap = last_overlap = None
        for i,(s_start,s_length) in enumerate(self._spans):
            #print "  (%d+%d)-> overlap=%s adjacent=%s" % (s_start,s_length, overlap(s_start, s_length, start, length), adjacent(s_start, s_length, start, length))
            if (overlap(s_start, s_length, start, length)
                or adjacent(s_start, s_length, start, length)):
                last_overlap = i
                if first_overlap is None:
                    first_overlap = i
                continue
            # no overlap
            if first_overlap is not None:
                break
        #print "  first_overlap", first_overlap, last_overlap
        if first_overlap is None:
            # no overlap, so just insert the span and sort by starting
            # position.
            self._spans.insert(0, (start,length))
            self._spans.sort()
        else:
            # everything from [first_overlap] to [last_overlap] overlapped
            first_start,first_length = self._spans[first_overlap]
            last_start,last_length = self._spans[last_overlap]
            newspan_start = min(start, first_start)
            newspan_end = max(start+length, last_start+last_length)
            newspan_length = newspan_end - newspan_start
            newspan = (newspan_start, newspan_length)
            self._spans[first_overlap:last_overlap+1] = [newspan]
        #print "  ADD done: %s" % self.dump()
        self._check()

        return self

    def remove(self, start, length):
        assert start >= 0
        assert length > 0
        #print " REMOVE [%d+%d -%d) from %s" % (start, length, start+length, self.dump())
        first_complete_overlap = last_complete_overlap = None
        for i,(s_start,s_length) in enumerate(self._spans):
            s_end = s_start + s_length
            o = overlap(s_start, s_length, start, length)
            if o:
                o_start, o_length = o
                o_end = o_start+o_length
                if o_start == s_start and o_end == s_end:
                    # delete this span altogether
                    if first_complete_overlap is None:
                        first_complete_overlap = i
                    last_complete_overlap = i
                elif o_start == s_start:
                    # we only overlap the left side, so trim the start
                    #    1111
                    #  rrrr
                    #    oo
                    # ->   11
                    new_start = o_end
                    new_end = s_end
                    assert new_start > s_start
                    new_length = new_end - new_start
                    self._spans[i] = (new_start, new_length)
                elif o_end == s_end:
                    # we only overlap the right side
                    #    1111
                    #      rrrr
                    #      oo
                    # -> 11
                    new_start = s_start
                    new_end = o_start
                    assert new_end < s_end
                    new_length = new_end - new_start
                    self._spans[i] = (new_start, new_length)
                else:
                    # we overlap the middle, so create a new span. No need to
                    # examine any other spans.
                    #    111111
                    #      rr
                    #    LL  RR
                    left_start = s_start
                    left_end = o_start
                    left_length = left_end - left_start
                    right_start = o_end
                    right_end = s_end
                    right_length = right_end - right_start
                    self._spans[i] = (left_start, left_length)
                    self._spans.append( (right_start, right_length) )
                    self._spans.sort()
                    break
        if first_complete_overlap is not None:
            del self._spans[first_complete_overlap:last_complete_overlap+1]
        #print "  REMOVE done: %s" % self.dump()
        self._check()
        return self

    def dump(self):
        return "len=%d: %s" % (self.len(),
                               ",".join(["[%d-%d]" % (start,start+l-1)
                                         for (start,l) in self._spans]) )

    def each(self):
        for start, length in self._spans:
            for i in range(start, start+length):
                yield i

    def __iter__(self):
        for s in self._spans:
            yield s

    def __nonzero__(self): # this gets us bool()
        return bool(self.len())

    def len(self):
        # guess what! python doesn't allow __len__ to return a long, only an
        # int. So we stop using len(spans), use spans.len() instead.
        return sum([length for start,length in self._spans])

    def __add__(self, other):
        s = self.__class__(self)
        for (start, length) in other:
            s.add(start, length)
        return s

    def __sub__(self, other):
        s = self.__class__(self)
        for (start, length) in other:
            s.remove(start, length)
        return s

    def __iadd__(self, other):
        for (start, length) in other:
            self.add(start, length)
        return self

    def __isub__(self, other):
        for (start, length) in other:
            self.remove(start, length)
        return self

    def __and__(self, other):
        if not self._spans:
            return self.__class__()
        bounds = self.__class__(self._spans[0][0],
                                self._spans[-1][0]+self._spans[-1][1])
        not_other = bounds - other
        return self - not_other

    def __contains__(self, (start,length)):
        for span_start,span_length in self._spans:
            o = overlap(start, length, span_start, span_length)
            if o:
                o_start,o_length = o
                if o_start == start and o_length == length:
                    return True
        return False

def overlap(start0, length0, start1, length1):
    # return start2,length2 of the overlapping region, or None
    #  00      00   000   0000  00  00 000  00   00  00      00
    #     11    11   11    11   111 11 11  1111 111 11    11
    left = max(start0, start1)
    right = min(start0+length0, start1+length1)
    # if there is overlap, 'left' will be its start, and right-1 will
    # be the end'
    if left < right:
        return (left, right-left)
    return None

def adjacent(start0, length0, start1, length1):
    if (start0 < start1) and start0+length0 == start1:
        return True
    elif (start1 < start0) and start1+length1 == start0:
        return True
    return False

class DataSpans:
    """I represent portions of a large string. Equivalently, I can be said to
    maintain a large array of characters (with gaps of empty elements). I can
    be used to manage access to a remote share, where some pieces have been
    retrieved, some have been requested, and others have not been read.
    """

    def __init__(self, other=None):
        self.spans = [] # (start, data) tuples, non-overlapping, merged
        if other:
            for (start, data) in other.get_chunks():
                self.add(start, data)

    def __nonzero__(self): # this gets us bool()
        return bool(self.len())

    def len(self):
        # return number of bytes we're holding
        return sum([len(data) for (start,data) in self.spans])

    def _dump(self):
        # return iterator of sorted list of offsets, one per byte
        for (start,data) in self.spans:
            for i in range(start, start+len(data)):
                yield i

    def dump(self):
        return "len=%d: %s" % (self.len(),
                               ",".join(["[%d-%d]" % (start,start+len(data)-1)
                                         for (start,data) in self.spans]) )

    def get_chunks(self):
        return list(self.spans)

    def get_spans(self):
        """Return a Spans object with a bit set for each byte I hold"""
        return Spans([(start, len(data)) for (start,data) in self.spans])

    def assert_invariants(self):
        if not self.spans:
            return
        prev_start = self.spans[0][0]
        prev_end = prev_start + len(self.spans[0][1])
        for start, data in self.spans[1:]:
            if not start > prev_end:
                # adjacent or overlapping: bad
                print "ASSERTION FAILED", self.spans
                raise AssertionError

    def get(self, start, length):
        # returns a string of LENGTH, or None
        #print "get", start, length, self.spans
        end = start+length
        for (s_start,s_data) in self.spans:
            s_end = s_start+len(s_data)
            #print " ",s_start,s_end
            if s_start <= start < s_end:
                # we want some data from this span. Because we maintain
                # strictly merged and non-overlapping spans, everything we
                # want must be in this span.
                offset = start - s_start
                if offset + length > len(s_data):
                    #print " None, span falls short"
                    return None # span falls short
                #print " some", s_data[offset:offset+length]
                return s_data[offset:offset+length]
            if s_start >= end:
                # we've gone too far: no further spans will overlap
                #print " None, gone too far"
                return None
        #print " None, ran out of spans"
        return None

    def add(self, start, data):
        # first: walk through existing spans, find overlap, modify-in-place
        #  create list of new spans
        #  add new spans
        #  sort
        #  merge adjacent spans
        #print "add", start, data, self.spans
        end = start + len(data)
        i = 0
        while len(data):
            #print " loop", start, data, i, len(self.spans), self.spans
            if i >= len(self.spans):
                #print " append and done"
                # append a last span
                self.spans.append( (start, data) )
                break
            (s_start,s_data) = self.spans[i]
            # five basic cases:
            #  a: OLD  b:OLDD  c1:OLD  c2:OLD   d1:OLDD  d2:OLD  e: OLLDD
            #    NEW     NEW      NEW     NEWW      NEW      NEW     NEW
            #
            # we handle A by inserting a new segment (with "N") and looping,
            # turning it into B or C. We handle B by replacing a prefix and
            # terminating. We handle C (both c1 and c2) by replacing the
            # segment (and, for c2, looping, turning it into A). We handle D
            # by replacing a suffix (and, for d2, looping, turning it into
            # A). We handle E by replacing the middle and terminating.
            if start < s_start:
                # case A: insert a new span, then loop with the remainder
                #print " insert new span"
                s_len = s_start-start
                self.spans.insert(i, (start, data[:s_len]))
                i += 1
                start = s_start
                data = data[s_len:]
                continue
            s_len = len(s_data)
            s_end = s_start+s_len
            if s_start <= start < s_end:
                #print " modify this span", s_start, start, s_end
                # we want to modify some data in this span: a prefix, a
                # suffix, or the whole thing
                if s_start == start:
                    if s_end <= end:
                        #print " replace whole segment"
                        # case C: replace this segment
                        self.spans[i] = (s_start, data[:s_len])
                        i += 1
                        start += s_len
                        data = data[s_len:]
                        # C2 is where len(data)>0
                        continue
                    # case B: modify the prefix, retain the suffix
                    #print " modify prefix"
                    self.spans[i] = (s_start, data + s_data[len(data):])
                    break
                if start > s_start and end < s_end:
                    # case E: modify the middle
                    #print " modify middle"
                    prefix_len = start - s_start # we retain this much
                    suffix_len = s_end - end # and retain this much
                    newdata = s_data[:prefix_len] + data + s_data[-suffix_len:]
                    self.spans[i] = (s_start, newdata)
                    break
                # case D: retain the prefix, modify the suffix
                #print " modify suffix"
                prefix_len = start - s_start # we retain this much
                suffix_len = s_len - prefix_len # we replace this much
                #print "  ", s_data, prefix_len, suffix_len, s_len, data
                self.spans[i] = (s_start,
                                 s_data[:prefix_len] + data[:suffix_len])
                i += 1
                start += suffix_len
                data = data[suffix_len:]
                #print "  now", start, data
                # D2 is where len(data)>0
                continue
            # else we're not there yet
            #print " still looking"
            i += 1
            continue
        # now merge adjacent spans
        #print " merging", self.spans
        newspans = []
        for (s_start,s_data) in self.spans:
            if newspans and adjacent(newspans[-1][0], len(newspans[-1][1]),
                                     s_start, len(s_data)):
                newspans[-1] = (newspans[-1][0], newspans[-1][1] + s_data)
            else:
                newspans.append( (s_start, s_data) )
        self.spans = newspans
        self.assert_invariants()
        #print " done", self.spans

    def remove(self, start, length):
        i = 0
        end = start + length
        #print "remove", start, length, self.spans
        while i < len(self.spans):
            (s_start,s_data) = self.spans[i]
            if s_start >= end:
                # this segment is entirely right of the removed region, and
                # all further segments are even further right. We're done.
                break
            s_len = len(s_data)
            s_end = s_start + s_len
            o = overlap(start, length, s_start, s_len)
            if not o:
                i += 1
                continue
            o_start, o_len = o
            o_end = o_start + o_len
            if o_len == s_len:
                # remove the whole segment
                del self.spans[i]
                continue
            if o_start == s_start:
                # remove a prefix, leaving the suffix from o_end to s_end
                prefix_len = o_end - o_start
                self.spans[i] = (o_end, s_data[prefix_len:])
                i += 1
                continue
            elif o_end == s_end:
                # remove a suffix, leaving the prefix from s_start to o_start
                prefix_len = o_start - s_start
                self.spans[i] = (s_start, s_data[:prefix_len])
                i += 1
                continue
            # remove the middle, creating a new segment
            # left is s_start:o_start, right is o_end:s_end
            left_len = o_start - s_start
            left = s_data[:left_len]
            right_len = s_end - o_end
            right = s_data[-right_len:]
            self.spans[i] = (s_start, left)
            self.spans.insert(i+1, (o_end, right))
            break
        #print " done", self.spans

    def pop(self, start, length):
        data = self.get(start, length)
        if data:
            self.remove(start, length)
        return data
