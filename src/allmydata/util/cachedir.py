
import os.path, stat, weakref, time
from twisted.application import service, internet
from allmydata.util import fileutil

HOUR = 60*60

class CacheDirectoryManager(service.MultiService):
    def __init__(self, basedir, pollinterval=1*HOUR, old=1*HOUR):
        service.MultiService.__init__(self)
        self.basedir = basedir
        fileutil.make_dirs(basedir)
        self.old = old
        self.files = weakref.WeakValueDictionary()

        t = internet.TimerService(pollinterval, self.check)
        t.setServiceParent(self)

    def get_file(self, key):
        assert isinstance(key, str) # used as filename
        absfn = os.path.join(self.basedir, key)
        if os.path.exists(absfn):
            os.utime(absfn, None)
        cf = CacheFile(absfn)
        self.files[key] = cf
        return cf

    def check(self):
        now = time.time()
        for fn in os.listdir(self.basedir):
            if fn in self.files:
                continue
            absfn = os.path.join(self.basedir, fn)
            mtime = os.stat(absfn)[stat.ST_MTIME]
            if now - mtime > self.old:
                os.remove(absfn)

class CacheFile:
    def __init__(self, absfn):
        self.filename = absfn

    def get_filename(self):
        return self.filename
