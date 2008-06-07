"""
A read-only svn fs showing all the revisions in subdirectories.
"""
from objectfs import ObjectFs, SymLink
from handler import Handler
from pysvn.ra import connect
from pysvn.date import decode
import errno, posixpath, time


#USE_SYMLINKS = 0      # they are wrong if the original file had another path

# use  getfattr -d filename  to see the node's attributes, which include
# information like the revision at which the file was last modified


class Root:
    def __init__(self, svnurl):
        self.svnurl = svnurl
        self.ra = connect(svnurl)
        self.head = self.ra.get_latest_rev()

    def listdir(self):
        for rev in range(1, self.head+1):
            yield str(rev)
        yield 'HEAD'

    def join(self, name):
        try:
            rev = int(name)
        except ValueError:
            if name == 'HEAD':
                return SymLink(str(self.head))
            else:
                raise KeyError(name)
        return TopLevelDir(self.ra, rev, rev, '')


class Node:
    def __init__(self, ra, rev, last_changed_rev, path):
        self.ra = ra
        self.rev = rev
        self.last_changed_rev = last_changed_rev
        self.path = path

    def __repr__(self):
        return '<%s %d/%s>' % (self.__class__.__name__, self.rev, self.path)

class Dir(Node):
    def listdir(self):
        rev, props, entries = self.ra.get_dir(self.path, self.rev,
                                              want_props = False)
        for key, stats in entries.items():
            yield key, getnode(self.ra, self.rev,
                               posixpath.join(self.path, key), stats)

class File(Node):
    def __init__(self, ra, rev, last_changed_rev, path, size):
        Node.__init__(self, ra, rev, last_changed_rev, path)
        self.filesize = size

    def size(self):
        return self.filesize

    def read(self):
        checksum, rev, props, data = self.ra.get_file(self.path, self.rev,
                                                      want_props = False)
        return data


class TopLevelDir(Dir):
    def listdir(self):
        for item in Dir.listdir(self):
            yield item
        yield 'svn:log', Log(self.ra, self.rev)

class Log:

    def __init__(self, ra, rev):
        self.ra = ra
        self.rev = rev

    def getlogentry(self):
        try:
            return self.logentry
        except AttributeError:
            logentries = self.ra.log('', startrev=self.rev, endrev=self.rev)
            try:
                [self.logentry] = logentries
            except ValueError:
                self.logentry = None
            return self.logentry

    def size(self):
        return len(self.read())

    def read(self):
        logentry = self.getlogentry()
        if logentry is None:
            return 'r%d | (no change here)\n' % (self.rev,)
        datetuple = time.gmtime(decode(logentry.date))
        date = time.strftime("%c", datetuple)
        return 'r%d | %s | %s\n\n%s' % (self.rev,
                                        logentry.author,
                                        date,
                                        logentry.message)


if 0:
    pass
##if USE_SYMLINKS:
##    def getnode(ra, rev, path, stats):
##        committed_rev = stats['svn:entry:committed-rev']
##        if committed_rev == rev:
##            kind = stats['svn:entry:kind']
##            if kind == 'file':
##                return File(ra, rev, path, stats['svn:entry:size'])
##            elif kind == 'dir':
##                return Dir(ra, rev, path)
##            else:
##                raise IOError(errno.EINVAL, "kind %r" % (kind,))
##        else:
##            depth = path.count('/')
##            return SymLink('../' * depth + '../%d/%s' % (committed_rev, path))
else:
    def getnode(ra, rev, path, stats):
        last_changed_rev = stats['svn:entry:committed-rev']
        kind = stats['svn:entry:kind']
        if kind == 'file':
            return File(ra, rev, last_changed_rev, path,
                        stats['svn:entry:size'])
        elif kind == 'dir':
            return Dir(ra, rev, last_changed_rev, path)
        else:
            raise IOError(errno.EINVAL, "kind %r" % (kind,))


if __name__ == '__main__':
    import sys
    svnurl, mountpoint = sys.argv[1:]
    handler = Handler(mountpoint, ObjectFs(Root(svnurl)))
    handler.loop_forever()
