import collections, itertools, functools

objnums = collections.defaultdict(itertools.count)


@functools.total_ordering
class NummedObj(object):
    """
    This is useful for nicer debug printouts.  Instead of objects of the same class being
    distinguished from one another by their memory address, they each get a unique number, which
    can be read as "the first object of this class", "the second object of this class", etc.  This
    is especially useful because separate runs of a program will yield identical debug output,
    (assuming that the objects get created in the same order in each run).  This makes it possible
    to diff outputs from separate runs to see what changed, without having to ignore a difference
    on every line due to different memory addresses of objects.
    """

    def __init__(self, klass=None):
        """
        @param klass: in which class are you counted?  If default value of `None', then self.__class__ will be used.
        """
        if klass is None:
            klass = self.__class__
        self._classname = klass.__name__

        self._objid = objnums[self._classname].next()

    def __repr__(self):
        return "<%s #%d>" % (self._classname, self._objid,)

    def __lt__(self, other):
        if isinstance(other, NummedObj):
            return (self._objid, self._classname,) < (other._objid, other._classname,)
        return NotImplemented

    def __eq__(self, other):
        if isinstance(other, NummedObj):
            return (self._objid, self._classname,) == (other._objid, other._classname,)
        return NotImplemented

    def __hash__(self):
        return id(self)
