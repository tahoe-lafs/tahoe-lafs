import py
from handler import Handler
from objectfs import ObjectFs


class SvnDir:
    def __init__(self, path):
        self.path = path

    def listdir(self):
        for p in self.path.listdir():
            if p.check(dir=1):
                cls = SvnDir
            else:
                cls = SvnFile
            yield p.basename, cls(p)


class SvnFile:
    data = None

    def __init__(self, path):
        self.path = path

    def size(self):
        if self.data is None:
            return None
        else:
            return len(self.data)

    def read(self):
        if self.data is None:
            self.data = self.path.read()
        return self.data


if __name__ == '__main__':
    import sys
    svnurl, mountpoint = sys.argv[1:]
    root = SvnDir(py.path.svnurl(svnurl))
    handler = Handler(mountpoint, ObjectFs(root))
    handler.loop_forever()
