
from zope.interface import implements
from twisted.application import service
from foolscap import Referenceable
from allmydata.interfaces import RIControlClient
from allmydata.util import testutil


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

    def remote_get_memory_usage(self):
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
