
from allmydata.util.assertutil import _assert


def concat(seqs):
    """
    O(n), rather than O(n^2), concatenation of list-like things, returning a list.
    I can't believe this isn't built in.
    """
    total_len = 0
    for seq in seqs:
        total_len += len(seq)
    result = [None]*total_len
    i = 0
    for seq in seqs:
        for x in seq:
            result[i] = x
            i += 1
    _assert(i == total_len, i=i, total_len=total_len)
    return result
