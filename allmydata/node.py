
from twisted.application import service
import os.path
from foolscap import Tub
from allmydata.util.iputil import get_local_ip_for
from allmydata.util import idlib
from twisted.python import log

class Node(service.MultiService):
    # this implements common functionality of both Client nodes and the Queen
    # node.
    NODETYPE = "unknown NODETYPE"
    PORTNUMFILE = None
    CERTFILE = None
    LOCAL_IP_FILE = "local_ip"

    def __init__(self, basedir="."):
        service.MultiService.__init__(self)
        self.basedir = os.path.abspath(basedir)
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
        self.nodeid = idlib.a2b(self.tub.tubID)
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
                log.msg("AuthorizedKeysManhole listening on %d" % portnum)

    def _setup_tub(self, local_ip):
        # we can't get a dynamically-assigned portnum until our Tub is
        # running, which means after startService.
        l = self.tub.getListeners()[0]
        portnum = l.getPortnum()
        local_ip_filename = os.path.join(self.basedir, self.LOCAL_IP_FILE)
        if os.path.exists(local_ip_filename):
            f = open(local_ip_filename, "r")
            local_ip = f.read()
            f.close()
        self.tub.setLocation("%s:%d" % (local_ip, portnum))
        if not os.path.exists(self._portnumfile):
            # record which port we're listening on, so we can grab the same
            # one next time
            f = open(self._portnumfile, "w")
            f.write("%d\n" % portnum)
            f.close()
        self.tub.setLocation("%s:%d" % (local_ip, l.getPortnum()))
        return self.tub

    def tub_ready(self):
        # called when the Tub is available for registerReference
        pass

    def add_service(self, s):
        s.setServiceParent(self)
        return s

    def startService(self):
        # note: this class can only be started and stopped once.
        service.MultiService.startService(self)
        local_ip = get_local_ip_for()
        self._setup_tub(local_ip)
        self.tub_ready()
        log.msg("%s running" % self.NODETYPE)
