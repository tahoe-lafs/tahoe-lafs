import hotshot.stats, os, random, sys

from pyutil import benchutil, randutil # http://tahoe-lafs.org/trac/pyutil

from zope.interface import implements
from allmydata import dirnode, uri
from allmydata.interfaces import IFileNode
from allmydata.mutable.filenode import MutableFileNode
from allmydata.immutable.filenode import ImmutableFileNode

class ContainerNode:
    implements(IFileNode)
    # dirnodes sit on top of a "container" filenode, from which it extracts a
    # writekey
    def __init__(self):
        self._writekey = randutil.insecurerandstr(16)
        self._fingerprint = randutil.insecurerandstr(32)
        self._cap = uri.WriteableSSKFileURI(self._writekey, self._fingerprint)
    def get_writekey(self):
        return self._writekey
    def get_cap(self):
        return self._cap
    def get_uri(self):
        return self._cap.to_string()
    def is_readonly(self):
        return False
    def is_mutable(self):
        return True

class FakeNode:
    def raise_error(self):
        return None

class FakeNodeMaker:
    def create_from_cap(self, writecap, readcap=None, deep_immutable=False, name=''):
        return FakeNode()

def random_unicode(n=140, b=3, codec='utf-8'):
    l = []
    while len(l) < n:
        try:
            u = os.urandom(b).decode(codec)[0]
        except UnicodeDecodeError:
            pass
        else:
            l.append(u)
    return u''.join(l)

encoding_parameters = {"k": 3, "n": 10}
def random_metadata():
    d = {}
    d['tahoe'] = {}
    d['tahoe']['linkcrtime'] = random.random()
    d['tahoe']['linkmotime'] = random.random()
    return d

PROF_FILE_NAME="bench_dirnode.prof"

class B(object):
    def __init__(self):
        self.children = [] # tuples of (k, v) (suitable for passing to dict())
        self.packstr = None
        self.nodemaker = FakeNodeMaker()
        self.testdirnode = dirnode.DirectoryNode(ContainerNode(), self.nodemaker, uploader=None)

    def random_fsnode(self):
        coin = random.randrange(0, 3)
        if coin == 0:
            cap = uri.CHKFileURI(randutil.insecurerandstr(16),
                                 randutil.insecurerandstr(32),
                                 random.randrange(1, 5),
                                 random.randrange(6, 15),
                                 random.randrange(99, 1000000000000))
            return ImmutableFileNode(cap, None, None, None, None, None)
        elif coin == 1:
            cap = uri.WriteableSSKFileURI(randutil.insecurerandstr(16),
                                          randutil.insecurerandstr(32))
            n = MutableFileNode(None, None, encoding_parameters, None)
            return n.init_from_cap(cap)
        else:
            assert coin == 2
            cap = uri.WriteableSSKFileURI(randutil.insecurerandstr(16),
                                          randutil.insecurerandstr(32))
            n = MutableFileNode(None, None, encoding_parameters, None)
            n.init_from_cap(cap)
            return dirnode.DirectoryNode(n, self.nodemaker, uploader=None)

    def random_child(self):
        return self.random_fsnode(), random_metadata()

    def init_for_pack(self, N):
        for i in xrange(len(self.children), N):
            name = random_unicode(random.randrange(0, 10))
            self.children.append( (name, self.random_child()) )

    def init_for_unpack(self, N):
        self.init_for_pack(N)
        self.packstr = self.pack(N)

    def pack(self, N):
        return self.testdirnode._pack_contents(dict(self.children[:N]))

    def unpack(self, N):
        return self.testdirnode._unpack_contents(self.packstr)

    def unpack_and_repack(self, N):
        return self.testdirnode._pack_contents(self.testdirnode._unpack_contents(self.packstr))

    def run_benchmarks(self, profile=False):
        for (initfunc, func) in [(self.init_for_unpack, self.unpack),
                                 (self.init_for_pack, self.pack),
                                 (self.init_for_unpack, self.unpack_and_repack)]:
            print "benchmarking %s" % (func,)
            for N in 16, 512, 2048, 16384:
                print "%5d" % N,
                benchutil.rep_bench(func, N, initfunc=initfunc, MAXREPS=20, UNITS_PER_SECOND=1000)
        benchutil.print_bench_footer(UNITS_PER_SECOND=1000)
        print "(milliseconds)"

    def prof_benchmarks(self):
        # This requires pyutil >= v1.3.34.
        self.run_benchmarks(profile=True)

    def print_stats(self):
        s = hotshot.stats.load(PROF_FILE_NAME)
        s.strip_dirs().sort_stats("time").print_stats(32)

if __name__ == "__main__":
    if '--profile' in sys.argv:
        if os.path.exists(PROF_FILE_NAME):
            print "WARNING: profiling results file '%s' already exists -- the profiling results from this run will be added into the profiling results stored in that file and then the sum of them will be printed out after this run." % (PROF_FILE_NAME,)
        b = B()
        b.prof_benchmarks()
        b.print_stats()
    else:
        b = B()
        b.run_benchmarks()
