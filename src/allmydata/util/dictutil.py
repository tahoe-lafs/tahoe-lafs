
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

