
import os.path
from zope.interface import implements
from twisted.application import service
from twisted.python import log
from foolscap import Referenceable, RemoteInterface
from foolscap.schema import DictOf, Any

class RILogObserver(RemoteInterface):
    def msg(logmsg=DictOf(str, Any())):
        return None
class RISubscription(RemoteInterface):
    pass

class RILogPublisher(RemoteInterface):
    def get_versions():
        return DictOf(str, str)
    def subscribe_to_all(observer=RILogObserver):
        return RISubscription
    def unsubscribe(subscription=Any()):
        # I don't know how to get the constraint right: unsubscribe() should
        # accept return value of subscribe_to_all()
        return None

class RILogGatherer(RemoteInterface):
    def logport(nodeid=str, logport=RILogPublisher):
        return None

class Subscription(Referenceable):
    implements(RISubscription)

class LogPublisher(Referenceable, service.MultiService):
    implements(RILogPublisher)
    name = "log_publisher"

    def __init__(self):
        service.MultiService.__init__(self)
        self._subscribers = {}
        self._notifyOnDisconnectors = {}

    def startService(self):
        service.MultiService.startService(self)
        furlfile = os.path.join(self.parent.basedir, "logport.furl")
        self.parent.tub.registerReference(self, furlFile=furlfile)
        os.chmod(furlfile, 0600)

        log.addObserver(self._twisted_log_observer)

    def stopService(self):
        log.removeObserver(self._twisted_log_observer)
        return service.MultiService.stopService(self)

    def _twisted_log_observer(self, d):
        # Twisted will remove this for us if it fails.

        # keys:
        #  ['message']: *args
        #  ['time']: float
        #  ['isError']: bool, usually False
        #  ['system']: string

        for o in self._subscribers.values():
            o.callRemoteOnly("msg", d)

        #f = open("/tmp/f.out", "a")
        #print >>f, d['message']
        #f.close()

    def remote_get_versions(self):
        versions = self.parent.get_versions()
        # our __version__ attributes are actually instances of
        # allmydata.util.version_class.Version, so convert them into strings
        # first.
        return dict([(k,str(v))
                     for k,v in versions.items()])

    def remote_subscribe_to_all(self, observer):
        s = Subscription()
        self._subscribers[s] = observer
        c = observer.notifyOnDisconnect(self.remote_unsubscribe, s)
        self._notifyOnDisconnectors[s] = c
        return s

    def remote_unsubscribe(self, s):
        observer = self._subscribers.pop(s)
        c = self._notifyOnDisconnectors.pop(s)
        observer.dontNotifyOnDisconnect(c)

