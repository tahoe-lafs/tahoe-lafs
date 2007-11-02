
import os.path
from zope.interface import implements
from twisted.application import service
from foolscap import Referenceable, RemoteInterface
from foolscap.schema import DictOf

class RILogPublisher(RemoteInterface):
    def get_versions():
        return DictOf(str, str)

class RILogGatherer(RemoteInterface):
    def logport(nodeid=str, logport=RILogPublisher):
        return None

class LogPublisher(Referenceable, service.MultiService):
    implements(RILogPublisher)
    name = "log_publisher"

    def __init__(self):
        service.MultiService.__init__(self)

    def startService(self):
        service.MultiService.startService(self)
        furlfile = os.path.join(self.parent.basedir, "logport.furl")
        self.parent.tub.registerReference(self, furlFile=furlfile)
        os.chmod(furlfile, 0600)

    def remote_get_versions(self):
        versions = self.parent.get_versions()
        # our __version__ attributes are actually instances of
        # allmydata.util.version_class.Version, so convert them into strings
        # first.
        return dict([(k,str(v))
                     for k,v in versions.items()])

