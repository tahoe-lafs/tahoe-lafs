
import os, time
from zope.interface import implements
from twisted.application import service
from foolscap import Referenceable
from allmydata.interfaces import RIControlClient
from allmydata.util import testutil, idlib
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
        def _check():
            current_clients = list(self.parent.get_all_peerids())
            return len(current_clients) >= num_clients
        d = self.poll(_check, 0.5)
        d.addCallback(lambda res: None)
        return d

    def remote_upload_from_file_to_uri(self, filename):
        uploader = self.parent.getServiceNamed("uploader")
        d = uploader.upload_filename(filename)
        return d

    def remote_download_from_uri_to_file(self, uri, filename):
        downloader = self.parent.getServiceNamed("downloader")
        d = downloader.download_to_filename(uri, filename)
        d.addCallback(lambda res: filename)
        return d

    def remote_upload_speed_test(self, size):
        """Write a tempfile to disk of the given size. Measure how long
        it takes to upload it to the servers.
        """
        assert size > 8
        fn = os.path.join(self.parent.basedir, idlib.b2a(os.urandom(8)))
        f = open(fn, "w")
        f.write(os.urandom(8))
        size -= 8
        while size > 0:
            chunk = min(size, 4096)
            f.write("\x00" * chunk)
            size -= chunk
        f.close()
        uploader = self.parent.getServiceNamed("uploader")
        start = time.time()
        d = uploader.upload_filename(fn)
        def _done(uri):
            stop = time.time()
            return stop - start
        d.addCallback(_done)
        def _cleanup(res):
            os.unlink(fn)
            return res
        d.addBoth(_cleanup)
        return d

    def remote_get_memory_usage(self):
        return get_memory_usage()
