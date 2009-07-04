import os, random

from pyutil import benchutil, randutil # http://allmydata.org/trac/pyutil

from allmydata import client, dirnode, uri
from allmydata.mutable import filenode as mut_filenode
from allmydata.immutable import filenode as immut_filenode

class FakeDownloadCache:
    def get_file(self, key):
        return None

class FakeClient(client.Client):
    # just enough
    def __init__(self):
        self._node_cache = {}
        self.download_cache = FakeDownloadCache()
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

class FakeDirectoryNode(dirnode.NewDirectoryNode):
    def __init__(self, client):
        dirnode.NewDirectoryNode.__init__(self, client)
        mutfileuri = uri.WriteableSSKFileURI(randutil.insecurerandstr(16), randutil.insecurerandstr(32))
        myuri = uri.NewDirectoryURI(mutfileuri)
        self.init_from_uri(myuri)


children = [] # tuples of (k, v) (suitable for passing to dict())
packstr = None
fakeclient = FakeClient()
testdirnode = dirnode.NewDirectoryNode(fakeclient)
testdirnode.init_from_uri(uri.NewDirectoryURI(uri.WriteableSSKFileURI(randutil.insecurerandstr(16), randutil.insecurerandstr(32))))

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
    return testdirnode._pack_contents(dict(children[:N]))

def unpack(N):
    return testdirnode._unpack_contents(packstr)

def run_benchmarks():
    print "benchmarking %s" % (unpack,)
    benchutil.bench(unpack, initfunc=init_for_unpack, TOPXP=12)
    print "benchmarking %s" % (pack,)
    benchutil.bench(pack, initfunc=init_for_pack, TOPXP=12)

def prof_benchmarks():
    import hotshot
    prof = hotshot.Profile("bench_dirnode.prof")
    prof.runcall(run_benchmarks)
    prof.close()

if __name__ == "__main__":
    run_benchmarks()
