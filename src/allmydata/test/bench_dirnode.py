import hotshot.stats, os, random, sys

from pyutil import benchutil, randutil # http://allmydata.org/trac/pyutil

from allmydata import client, dirnode, uri
from allmydata.mutable import filenode as mut_filenode
from allmydata.immutable import filenode as immut_filenode
from allmydata.util import cachedir, fileutil

class FakeClient(client.Client):
    # just enough
    def __init__(self):
        self._node_cache = {}
        download_cachedir = fileutil.NamedTemporaryDirectory()
        self.download_cache_dirman = cachedir.CacheDirectoryManager(download_cachedir.name)
    def getServiceNamed(self, name):
        return None
    def get_encoding_parameters(self):
        return {"k": 3, "n": 10}
    def get_writekey(self):
        return os.urandom(16)

class FakeMutableFileNode(mut_filenode.MutableFileNode):
    def __init__(self, client):
        mut_filenode.MutableFileNode.__init__(self, client)
        self._uri = uri.WriteableSSKFileURI(randutil.insecurerandstr(16), randutil.insecurerandstr(32))

class FakeDirectoryNode(dirnode.DirectoryNode):
    def __init__(self, client):
        dirnode.DirectoryNode.__init__(self, client)
        mutfileuri = uri.WriteableSSKFileURI(randutil.insecurerandstr(16), randutil.insecurerandstr(32))
        myuri = uri.DirectoryURI(mutfileuri)
        self.init_from_uri(myuri)


children = [] # tuples of (k, v) (suitable for passing to dict())
packstr = None
fakeclient = FakeClient()
testdirnode = dirnode.DirectoryNode(fakeclient)
testdirnode.init_from_uri(uri.DirectoryURI(uri.WriteableSSKFileURI(randutil.insecurerandstr(16), randutil.insecurerandstr(32))))

def random_unicode(l):
    while True:
        try:
            return os.urandom(l).decode('utf-8')
        except UnicodeDecodeError:
            pass

def random_fsnode():
    coin = random.randrange(0, 3)
    if coin == 0:
        return immut_filenode.FileNode(uri.CHKFileURI(randutil.insecurerandstr(16), randutil.insecurerandstr(32), random.randrange(1, 5), random.randrange(6, 15), random.randrange(99, 1000000000000)), fakeclient, None)
    elif coin == 1:
        return FakeMutableFileNode(fakeclient)
    else:
        assert coin == 2
        return FakeDirectoryNode(fakeclient)

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
        children.append((random_unicode(random.randrange(1, 9)), random_child()))

def init_for_unpack(N):
    global packstr
    init_for_pack(N)
    packstr = pack(N)

def pack(N):
    return testdirnode._pack_contents(dirnode.CachingDict(children[:N]))

def unpack(N):
    return testdirnode._unpack_contents(packstr)

def unpack_and_repack(N):
    return testdirnode._pack_contents(testdirnode._unpack_contents(packstr))

PROF_FILE_NAME="bench_dirnode.prof"

def run_benchmarks(profile=False):
    for (func, initfunc) in [(unpack, init_for_unpack), (pack, init_for_pack), (unpack_and_repack, init_for_unpack)]:
        print "benchmarking %s" % (func,)
        benchutil.bench(unpack_and_repack, initfunc=init_for_unpack, TOPXP=12, profile=profile, profresults=PROF_FILE_NAME)

def print_stats():
    s = hotshot.stats.load(PROF_FILE_NAME)
    s.strip_dirs().sort_stats("time").print_stats(32)

def prof_benchmarks():
    # This requires pyutil >= v1.3.34.
    run_benchmarks(profile=True)

if __name__ == "__main__":
    if '--profile' in sys.argv:
        if os.path.exists(PROF_FILE_NAME):
            print "WARNING: profiling results file '%s' already exists -- the profiling results from this run will be added into the profiling results stored in that file and then the sum of them will be printed out after this run."
        prof_benchmarks()
        print_stats()
    else:
        run_benchmarks()
