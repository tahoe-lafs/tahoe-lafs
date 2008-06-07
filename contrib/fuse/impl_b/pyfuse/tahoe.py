"""
PyFuse client for the Tahoe distributed file system.
See http://allmydata.org/
"""

# Read-only for now.

# Portions copied from the file contrib/fuse/tahoe_fuse.py distributed
# with Tahoe 1.0.0.

import os, sys
from objectfs import ObjectFs
from handler import Handler
import simplejson
import urllib


### Config:
TahoeConfigDir = '~/.tahoe'


### Utilities for debug:
def log(msg, *args):
    print msg % args


class TahoeConnection:
    def __init__(self, confdir):
        self.confdir = confdir
        self._init_url()

    def _init_url(self):
        f = open(os.path.join(self.confdir, 'webport'), 'r')
        contents = f.read()
        f.close()

        fields = contents.split(':')
        proto, port = fields[:2]
        assert proto == 'tcp'
        port = int(port)
        self.url = 'http://localhost:%d' % (port,)

    def get_root(self):
        # For now we just use the same default as the CLI:
        rootdirfn = os.path.join(self.confdir, 'private', 'root_dir.cap')
        f = open(rootdirfn, 'r')
        cap = f.read().strip()
        f.close()
        return TahoeDir(self, canonicalize_cap(cap))


class TahoeNode:
    def __init__(self, conn, uri):
        self.conn = conn
        self.uri = uri

    def get_metadata(self):
        f = self._open('?t=json')
        json = f.read()
        f.close()
        return simplejson.loads(json)

    def _open(self, postfix=''):
        url = '%s/uri/%s%s' % (self.conn.url, self.uri, postfix)
        log('*** Fetching: %r', url)
        return urllib.urlopen(url)


class TahoeDir(TahoeNode):
    def listdir(self):
        flag, md = self.get_metadata()
        assert flag == 'dirnode'
        result = []
        for name, (childflag, childmd) in md['children'].items():
            if childflag == 'dirnode':
                cls = TahoeDir
            else:
                cls = TahoeFile
            result.append((str(name), cls(self.conn, childmd['ro_uri'])))
        return result

class TahoeFile(TahoeNode):
    def size(self):
        rawsize = self.get_metadata()[1]['size']
        return rawsize

    def read(self):
        return self._open().read()


def canonicalize_cap(cap):
    cap = urllib.unquote(cap)
    i = cap.find('URI:')
    assert i != -1, 'A cap must contain "URI:...", but this does not: ' + cap
    return cap[i:]

def main(mountpoint, basedir):
    conn = TahoeConnection(basedir)
    root = conn.get_root()
    handler = Handler(mountpoint, ObjectFs(root))
    handler.loop_forever()

if __name__ == '__main__':
    [mountpoint] = sys.argv[1:]
    basedir = os.path.expanduser(TahoeConfigDir)
    main(mountpoint, basedir)
