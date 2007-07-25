from base64 import b32encode

import os.path, re

import twisted
from twisted.python import log
from twisted.application import service
from twisted.internet import defer, reactor
from foolscap import Tub, eventual
from allmydata.util import iputil, observer
from allmydata.util.assertutil import precondition


# Just to get their versions:
import allmydata
import zfec
import foolscap

# group 1 will be addr (dotted quad string), group 3 if any will be portnum (string)
ADDR_RE=re.compile("^([1-9][0-9]*\.[1-9][0-9]*\.[1-9][0-9]*\.[1-9][0-9]*)(:([1-9][0-9]*))?$")

class Node(service.MultiService):
    # this implements common functionality of both Client nodes, Introducer 
    # nodes, and Vdrive nodes
    NODETYPE = "unknown NODETYPE"
    PORTNUMFILE = None
    CERTFILE = "node.pem"
    LOCAL_IP_FILE = "advertised_ip_addresses"
    NODEIDFILE = "my_nodeid"

    def __init__(self, basedir="."):
        service.MultiService.__init__(self)
        self.basedir = os.path.abspath(basedir)
        self._tub_ready_observerlist = observer.OneShotObserverList()
        certfile = os.path.join(self.basedir, self.CERTFILE)
        self.tub = Tub(certFile=certfile)
        self.tub.setOption("logLocalFailures", True)
        self.tub.setOption("logRemoteFailures", True)
        self.nodeid = b32encode(self.tub.tubID).lower()
        f = open(os.path.join(self.basedir, self.NODEIDFILE), "w")
        f.write(b32encode(self.nodeid).lower() + "\n")
        f.close()
        self.short_nodeid = self.tub.tubID[:4] # ready for printing
        assert self.PORTNUMFILE, "Your node.Node subclass must provide PORTNUMFILE"
        self._portnumfile = os.path.join(self.basedir, self.PORTNUMFILE)
        try:
            portnum = int(open(self._portnumfile, "rU").read())
        except (EnvironmentError, ValueError):
            portnum = 0
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

        self.log("Node constructed.  tahoe version: %s, foolscap: %s,"
                 " twisted: %s, zfec: %s"
                 % (allmydata.__version__, foolscap.__version__,
                    twisted.__version__, zfec.__version__,))

    def get_versions(self):
        return {'allmydata': allmydata.__version__,
                'foolscap': foolscap.__version__,
                'twisted': twisted.__version__,
                'zfec': zfec.__version__,
                }

    def startService(self):
        # note: this class can only be started and stopped once.
        self.log("Node.startService")
        eventual.eventually(self._startService)

    def _startService(self):
        precondition(reactor.running)
        self.log("Node._startService")

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
        def _die(failure):
            self.log('_startService() failed')
            log.err(failure)
            #reactor.stop() # for unknown reasons, reactor.stop() isn't working.  [ ] TODO
            self.log('calling os.abort()')
            os.abort()
        d.addErrback(_die)

    def stopService(self):
        self.log("Node.stopService")
        d = self._tub_ready_observerlist.when_fired()
        def _really_stopService(ignored):
            self.log("Node._really_stopService")
            return service.MultiService.stopService(self)
        d.addCallback(_really_stopService)
        return d

    def shutdown(self):
        """Shut down the node. Returns a Deferred that fires (with None) when
        it finally stops kicking."""
        self.log("Node.shutdown")
        return self.stopService()

    def log(self, msg):
        log.msg(self.short_nodeid + ": " + msg)

    def _setup_tub(self, local_addresses):
        # we can't get a dynamically-assigned portnum until our Tub is
        # running, which means after startService.
        l = self.tub.getListeners()[0]
        portnum = l.getPortnum()
        # record which port we're listening on, so we can grab the same one next time
        open(self._portnumfile, "w").write("%d\n" % portnum)

        local_addresses = [ "%s:%d" % (addr, portnum,) for addr in local_addresses ]

        addresses = []
        try:
            for addrline in open(os.path.join(self.basedir, self.LOCAL_IP_FILE), "rU"):
                mo = ADDR_RE.search(addrline)
                if mo:
                    (addr, dummy, aportnum,) = mo.groups()
                    if aportnum is None:
                        aportnum = portnum
                    addresses.append("%s:%d" % (addr, int(aportnum),))
        except EnvironmentError:
            pass

        addresses.extend(local_addresses)

        location = ",".join(addresses)
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

