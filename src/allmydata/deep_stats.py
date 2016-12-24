"""Implementation of the deep stats class."""

import math

from allmydata.interfaces import IImmutableFileNode
from allmydata.interfaces import IMutableFileNode
from allmydata.interfaces import IDirectoryNode
from allmydata.unknown import UnknownNode
from allmydata.uri import LiteralFileURI
from allmydata.uri import from_string
from allmydata.util import mathutil

class DeepStats(object):
    """Deep stats object.

    Holds results of the deep-stats opetation.
    Used for json generation in the API."""

    # Json API version.
    # Rules:
    # - increment each time a field is removed or changes meaning.
    # - it's ok to add a new field without incrementing the version.
    API_VERSION = 1

    def __init__(self, origin):
        """Initializes DeepStats object. Sets most of the fields to 0."""
        self.monitor = None
        self.origin = origin
        self.stats = {
            'api-version': self.API_VERSION
        }
        for k in ["count-immutable-files",
                  "count-mutable-files",
                  "count-literal-files",
                  "count-files",
                  "count-directories",
                  "count-unknown",
                  "size-immutable-files",
                  #"size-mutable-files",
                  "size-literal-files",
                  "size-directories",
                  "largest-directory",
                  "largest-directory-children",
                  "largest-immutable-file",
                  #"largest-mutable-file",
                 ]:
            self.stats[k] = 0
        self.histograms = {}
        for k in ["size-files-histogram"]:
            self.histograms[k] = {} # maps (min,max) to count
        self.buckets = [(0, 0), (1, 3)]
        self.root = math.sqrt(10)

    def set_monitor(self, monitor):
        """Sets a new monitor."""
        self.monitor = monitor
        monitor.origin_si = self.origin.get_storage_index()
        monitor.set_status(self.get_results())

    def add_node(self, node, childpath):
        """Adds a node's stats to calculation."""
        if isinstance(node, UnknownNode):
            self.add("count-unknown")
        elif IDirectoryNode.providedBy(node):
            self.add("count-directories")
        elif IMutableFileNode.providedBy(node):
            self.add("count-files")
            self.add("count-mutable-files")
            # TODO: update the servermap, compute a size, add it to
            # size-mutable-files, max it into "largest-mutable-file"
        elif IImmutableFileNode.providedBy(node): # CHK and LIT
            self.add("count-files")
            size = node.get_size()
            self.histogram("size-files-histogram", size)
            theuri = from_string(node.get_uri())
            if isinstance(theuri, LiteralFileURI):
                self.add("count-literal-files")
                self.add("size-literal-files", size)
            else:
                self.add("count-immutable-files")
                self.add("size-immutable-files", size)
                self.max("largest-immutable-file", size)

    def enter_directory(self, parent, children):
        """Adds directory stats."""
        dirsize_bytes = parent.get_size()
        if dirsize_bytes is not None:
            self.add("size-directories", dirsize_bytes)
            self.max("largest-directory", dirsize_bytes)
        dirsize_children = len(children)
        self.max("largest-directory-children", dirsize_children)

    def add(self, key, value=1):
        self.stats[key] += value

    def max(self, key, value):
        self.stats[key] = max(self.stats[key], value)

    def which_bucket(self, size):
        # return (min,max) such that min <= size <= max
        # values are from the set (0,0), (1,3), (4,10), (11,31), (32,100),
        # (101,316), (317, 1000), etc: two per decade
        assert size >= 0
        i = 0
        while True:
            if i >= len(self.buckets):
                # extend the list
                new_lower = self.buckets[i-1][1]+1
                new_upper = int(mathutil.next_power_of_k(new_lower, self.root))
                self.buckets.append((new_lower, new_upper))
            maybe = self.buckets[i]
            if maybe[0] <= size <= maybe[1]:
                return maybe
            i += 1

    def histogram(self, key, size):
        bucket = self.which_bucket(size)
        h = self.histograms[key]
        if bucket not in h:
            h[bucket] = 0
        h[bucket] += 1

    def get_results(self):
        """Returns deep-stats resutls."""
        stats = self.stats.copy()
        for key in self.histograms:
            h = self.histograms[key]
            out = [ (bucket[0], bucket[1], h[bucket]) for bucket in h ]
            out.sort()
            stats[key] = out
        return stats

    def finish(self):
        """Finishes gathering stats."""
        return self.get_results()
