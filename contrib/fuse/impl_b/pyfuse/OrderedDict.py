from UserDict import DictMixin


DELETED = object()


class OrderedDict(DictMixin):

    def __init__(self, *args, **kwds):
        self.clear()
        self.update(*args, **kwds)

    def clear(self):
        self._keys = []
        self._content = {}    # {key: (index, value)}
        self._deleted = 0

    def copy(self):
        return OrderedDict(self)

    def __iter__(self):
        for key in self._keys:
            if key is not DELETED:
                yield key

    def keys(self):
        return [key for key in self._keys if key is not DELETED]

    def popitem(self):
        while 1:
            try:
                k = self._keys.pop()
            except IndexError:
                raise KeyError, 'OrderedDict is empty'
            if k is not DELETED:
                return k, self._content.pop(k)[1]

    def __getitem__(self, key):
        index, value = self._content[key]
        return value

    def __setitem__(self, key, value):
        try:
            index, oldvalue = self._content[key]
        except KeyError:
            index = len(self._keys)
            self._keys.append(key)
        self._content[key] = index, value

    def __delitem__(self, key):
        index, oldvalue = self._content.pop(key)
        self._keys[index] = DELETED
        if self._deleted <= len(self._content):
            self._deleted += 1
        else:
            # compress
            newkeys = []
            for k in self._keys:
                if k is not DELETED:
                    i, value = self._content[k]
                    self._content[k] = len(newkeys), value
                    newkeys.append(k)
            self._keys = newkeys
            self._deleted = 0

    def __len__(self):
        return len(self._content)

    def __repr__(self):
        res = ['%r: %r' % (key, self._content[key][1]) for key in self]
        return 'OrderedDict(%s)' % (', '.join(res),)

    def __cmp__(self, other):
        if not isinstance(other, OrderedDict):
            return NotImplemented
        keys = self.keys()
        r = cmp(keys, other.keys())
        if r:
            return r
        for k in keys:
            r = cmp(self[k], other[k])
            if r:
                return r
        return 0
