import fec

# div_ceil() was copied from the pyutil library.
def div_ceil(n, d):
    """
    The smallest integer k such that k*d >= n.
    """
    return (n/d) + (n%d != 0)


class Encoder(object):
    def __init__(self, k, m):
        self.fec = fec.Encoder(k, m)

    def encode(self, data):
        """
        @param data: string
        """
        chunksize = div_ceil(len(data), self.fec.k)
        numchunks = div_ceil(len(data), chunksize)
        l = [ data[i:i+chunksize] for i in range(0, len(data), chunksize) ]
        # padding
        if len(l[-1]) != len(l[0]):
            l[-1] = l[-1] + ('\x00'*(len(l[0])-len(l[-1])))
        return self.fec.encode(l)
        
    def decode(self, shares):
        return self.fec.decode(shares)
        
