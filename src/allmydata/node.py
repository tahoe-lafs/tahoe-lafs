
import os.path
from twisted.python import log
from twisted.application import service
from twisted.internet import defer
from foolscap import Tub
from allmydata.util import idlib, observer, iputil

class Node(service.MultiService):
    # this implements common functionality of both Client nodes and the Queen
    # node.
    NODETYPE = "unknown NODETYPE"
    PORTNUMFILE = None
    CERTFILE = None
    LOCAL_IP_FILE = "local_ip"
    NODEIDFILE = "my_nodeid"

    def __init__(self, basedir="."):
        service.MultiService.__init__(self)
        self.basedir = os.path.abspath(basedir)
        self._tub_ready_observerlist = observer.OneShotObserverList()
        assert self.CERTFILE, "Your node.Node subclass must provide CERTFILE"
        certfile = os.path.join(self.basedir, self.CERTFILE)
        if os.path.exists(certfile):
            f = open(certfile, "rb")
            self.tub = Tub(certData=f.read())
            f.close()
        else:
            self.tub = Tub()
            f = open(certfile, "wb")
            f.write(self.tub.getCertData())
            f.close()
        if False: # TODO: once foolscap-0.1.1 is released, enable this
            self.tub.setOption("logLocalFailures", True)
            self.tub.setOption("logRemoteFailures", True)
        self.nodeid = idlib.a2b(self.tub.tubID)
        f = open(os.path.join(self.basedir, self.NODEIDFILE), "w")
        f.write(idlib.b2a(self.nodeid) + "\n")
        f.close()
        self.short_nodeid = self.tub.tubID[:4] # ready for printing
        portnum = 0
        assert self.PORTNUMFILE, "Your node.Node subclass must provide PORTNUMFILE"
        self._portnumfile = os.path.join(self.basedir, self.PORTNUMFILE)
        if os.path.exists(self._portnumfile):
            portnum = int(open(self._portnumfile, "r").read())
        self.tub.listenOn("tcp:%d" % portnum)
        # we must wait until our service has started before we can find out
        # our IP address and thus do tub.setLocation, and we can't register
        # any services with the Tub until after that point
        self.tub.setServiceParent(self)

        AUTHKEYSFILEBASE = "authorized_keys."
        for f in os.listdir(self.basedir):
            if f.startswith(AUTHKEYSFILEBASE):
                keyfile = os.path.join(self.basedir, f)
                portnum = int(f[len(AUTHKEYSFILEBASE):])
                from allmydata import manhole
                m = manhole.AuthorizedKeysManhole(portnum, keyfile)
                m.setServiceParent(self)
                self.log("AuthorizedKeysManhole listening on %d" % portnum)

    def startService(self):
        """Start the node. Returns a Deferred that fires (with self) when it
        is ready to go.

        Many callers don't pay attention to the return value from
        startService, since they aren't going to do anything special when it
        finishes. If they are (for example unit tests which need to wait for
        the node to fully start up before it gets shut down), they can wait
        for the Deferred I return to fire. In particular, you should wait for
        my startService() Deferred to fire before you call my stopService()
        method.
        """

        # note: this class can only be started and stopped once.
        service.MultiService.startService(self)
        d = defer.succeed(None)
        d.addCallback(lambda res: iputil.get_local_addresses_async())
        d.addCallback(self._setup_tub)
        d.addCallback(lambda res: self.tub_ready())
        def _ready(res):
            self.log("%s running" % self.NODETYPE)
            self._tub_ready_observerlist.fire(self)
            return self
        d.addCallback(_ready)
        return d

    def shutdown(self):
        """Shut down the node. Returns a Deferred that fires (with None) when
        it finally stops kicking."""
        return self.stopService()

    def log(self, msg):
        log.msg(self.short_nodeid + ": " + msg)

    def _setup_tub(self, local_addresses):
        # we can't get a dynamically-assigned portnum until our Tub is
        # running, which means after startService.
        l = self.tub.getListeners()[0]
        portnum = l.getPortnum()
        local_ip_filename = os.path.join(self.basedir, self.LOCAL_IP_FILE)
        if os.path.exists(local_ip_filename):
            f = open(local_ip_filename, "r")
            local_ip = f.read()
            f.close()
            if local_ip not in local_addresses:
                local_addresses.append(local_ip)
        if not os.path.exists(self._portnumfile):
            # record which port we're listening on, so we can grab the same
            # one next time
            f = open(self._portnumfile, "w")
            f.write("%d\n" % portnum)
            f.close()
        location = ",".join(["%s:%d" % (ip, portnum)
                             for ip in local_addresses])
        self.log("Tub location set to %s" % location)
        self.tub.setLocation(location)
        return self.tub

    def tub_ready(self):
        # called when the Tub is available for registerReference
        pass

    def when_tub_ready(self):
        return self._tub_ready_observerlist.when_fired()

    def add_service(self, s):
        s.setServiceParent(self)
        return s

