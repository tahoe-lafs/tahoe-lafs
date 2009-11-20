import hotshot.stats, os, random, sys

from pyutil import benchutil, randutil # http://allmydata.org/trac/pyutil

from zope.interface import implements
from allmydata import dirnode, uri
from allmydata.interfaces import IFileNode
from allmydata.mutable.filenode import MutableFileNode
from allmydata.immutable.filenode import ImmutableFileNode

children = [] # tuples of (k, v) (suitable for passing to dict())
packstr = None

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
class FakeNodeMaker:
    def create_from_cap(self, writecap, readcap=None):
        return None

nodemaker = FakeNodeMaker()
testdirnode = dirnode.DirectoryNode(ContainerNode(), nodemaker, uploader=None)

def random_unicode(l):
    while True:
        try:
            return os.urandom(l).decode('utf-8')
        except UnicodeDecodeError:
            pass

encoding_parameters = {"k": 3, "n": 10}
def random_fsnode():
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
        return dirnode.DirectoryNode(n, nodemaker, uploader=None)

def random_metadata():
    d = {}
    d['ctime'] = random.random()
    d['mtime'] = random.random()
    d['tahoe'] = {}
    d['tahoe']['linkcrtime'] = random.random()
    d['tahoe']['linkmotime'] = random.random()
    return d

def random_child():
    return random_fsnode(), random_metadata()

def init_for_pack(N):
    for i in xrange(len(children), N):
        name = random_unicode(random.randrange(1, 9))
        children.append( (name, random_child()) )

def init_for_unpack(N):
    global packstr
    init_for_pack(N)
    packstr = pack(N)

def pack(N):
    return testdirnode._pack_contents(dict(children[:N]))

def unpack(N):
    return testdirnode._unpack_contents(packstr)

def unpack_and_repack(N):
    return testdirnode._pack_contents(testdirnode._unpack_contents(packstr))

PROF_FILE_NAME="bench_dirnode.prof"

def run_benchmarks(profile=False):
    for (initfunc, func) in [(init_for_unpack, unpack),
                             (init_for_pack, pack),
                             (init_for_unpack, unpack_and_repack)]:
        print "benchmarking %s" % (func,)
        benchutil.bench(unpack_and_repack, initfunc=init_for_unpack,
                        TOPXP=12)#, profile=profile, profresults=PROF_FILE_NAME)

def print_stats():
    s = hotshot.stats.load(PROF_FILE_NAME)
    s.strip_dirs().sort_stats("time").print_stats(32)

def prof_benchmarks():
    # This requires pyutil >= v1.3.34.
    run_benchmarks(profile=True)

if __name__ == "__main__":
    if '--profile' in sys.argv:
        if os.path.exists(PROF_FILE_NAME):
            print "WARNING: profiling results file '%s' already exists -- the profiling results from this run will be added into the profiling results stored in that file and then the sum of them will be printed out after this run." % (PROF_FILE_NAME,)
        prof_benchmarks()
        print_stats()
    else:
        run_benchmarks()
