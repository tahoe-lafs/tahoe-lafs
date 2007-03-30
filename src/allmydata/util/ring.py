
def distance(p1, p2, FULL = 2**160, HALF = 2**159):
    """
    Distance between two points in the space, expressed as longs.

    @param p1: long of first point
    @param p2: long of second point
    """
    d = p2 - p1
    if d < 0:
        d = FULL + d
    return d

