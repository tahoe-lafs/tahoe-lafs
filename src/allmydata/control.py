
import os, time
from zope.interface import implements
from twisted.application import service
from twisted.internet import defer
from foolscap import Referenceable
from allmydata.interfaces import RIControlClient
from allmydata.util import testutil, fileutil
from twisted.python import log

def get_memory_usage():
    # this is obviously linux-specific
    stat_names = ("VmPeak",
                  "VmSize",
                  #"VmHWM",
                  "VmData")
    stats = {}
    for line in open("/proc/self/status", "r").readlines():
        name, right = line.split(":",2)
        if name in stat_names:
            assert right.endswith(" kB\n")
            right = right[:-4]
            stats[name] = int(right) * 1024
    return stats

def log_memory_usage(where=""):
    stats = get_memory_usage()
    log.msg("VmSize: %9d  VmPeak: %9d  %s" % (stats["VmSize"],
                                              stats["VmPeak"],
                                              where))


class ControlServer(Referenceable, service.Service, testutil.PollMixin):
    implements(RIControlClient)

    def remote_wait_for_client_connections(self, num_clients):
        return self.parent.debug_wait_for_client_connections(num_clients)

    def remote_upload_from_file_to_uri(self, filename):
        uploader = self.parent.getServiceNamed("uploader")
        d = uploader.upload_filename(filename)
        return d

    def remote_download_from_uri_to_file(self, uri, filename):
        downloader = self.parent.getServiceNamed("downloader")
        d = downloader.download_to_filename(uri, filename)
        d.addCallback(lambda res: filename)
        return d

    def remote_upload_speed_test(self, count, size):
        assert size > 8
        basedir = os.path.join(self.parent.basedir, "_speed_test_data")
        log.msg("speed_test: count=%d, size=%d" % (count, size))
        fileutil.make_dirs(basedir)
        for i in range(count):
            s = size
            fn = os.path.join(basedir, str(i))
            if os.path.exists(fn):
                os.unlink(fn)
            f = open(fn, "w")
            f.write(os.urandom(8))
            s -= 8
            while s > 0:
                chunk = min(s, 4096)
                f.write("\x00" * chunk)
                s -= chunk
            f.close()
        uploader = self.parent.getServiceNamed("uploader")
        start = time.time()
        d = defer.succeed(None)
        def _do_one_file(uri, i):
            if i >= count:
                return
            fn = os.path.join(basedir, str(i))
            d1 = uploader.upload_filename(fn)
            d1.addCallback(_do_one_file, i+1)
            return d1
        d.addCallback(_do_one_file, 0)
        def _done(ignored):
            stop = time.time()
            return stop - start
        d.addCallback(_done)
        def _cleanup(res):
            for i in range(count):
                fn = os.path.join(basedir, str(i))
                os.unlink(fn)
            return res
        d.addBoth(_cleanup)
        return d

    def remote_get_memory_usage(self):
        return get_memory_usage()
