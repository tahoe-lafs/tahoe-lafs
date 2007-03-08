
from zope.interface import implements
from twisted.application import service
from foolscap import Referenceable
from allmydata.interfaces import RIControlClient


class ControlServer(Referenceable, service.Service):
    implements(RIControlClient)

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
                stats[name] = int(right.strip()) * 1024
        return stats
