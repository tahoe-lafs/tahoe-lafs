
import os, sha
from foolscap import Referenceable
from twisted.application import service
from twisted.python import log
from zope.interface import implements
from allmydata.interfaces import RIClient
from allmydata import node

from twisted.internet import defer

from allmydata.util import idlib
from allmydata.storageserver import StorageServer
from allmydata.upload import Uploader
from allmydata.download import Downloader
from allmydata.vdrive import VDrive
from allmydata.webish import WebishServer
from allmydata.control import ControlServer

class Client(node.Node, Referenceable):
    implements(RIClient)
    CERTFILE = "client.pem"
    PORTNUMFILE = "client.port"
    STOREDIR = 'storage'
    NODETYPE = "client"
    WEBPORTFILE = "webport"
    QUEEN_PBURL_FILE = "roster_pburl"

    def __init__(self, basedir="."):
        node.Node.__init__(self, basedir)
        self.queen = None # self.queen is either None or a RemoteReference
        self.introducer_client = None
        self.add_service(StorageServer(os.path.join(basedir, self.STOREDIR)))
        self.add_service(Uploader())
        self.add_service(Downloader())
        self.add_service(VDrive())
        WEBPORTFILE = os.path.join(self.basedir, self.WEBPORTFILE)
        if os.path.exists(WEBPORTFILE):
            f = open(WEBPORTFILE, "r")
            webport = f.read() # strports string
            f.close()
            self.add_service(WebishServer(webport))
        self.queen_pburl = None
        QUEEN_PBURL_FILE = os.path.join(self.basedir, self.QUEEN_PBURL_FILE)
        if os.path.exists(QUEEN_PBURL_FILE):
            f = open(QUEEN_PBURL_FILE, "r")
            self.queen_pburl = f.read().strip()
            f.close()
        self.queen_connector = None

    def tub_ready(self):
        self.my_pburl = self.tub.registerReference(self)
        if self.queen_pburl:
            self.introducer_client = IntroducerClient(self.tub, self.queen_pburl, self.my_pburl)
        self.register_control()
        self.maybe_connect_to_queen()

    def set_queen_pburl(self, queen_pburl):
        self.queen_pburl = queen_pburl
        self.maybe_connect_to_queen()

    def maybe_connect_to_queen(self):
        if not self.running:
            return
        if not self.my_pburl:
            return
        if self.queen_connector:
            return
        if not self.queen_pburl:
            self.log("no queen_pburl, cannot connect")
            return
        self.queen_connector = self.tub.connectTo(self.queen_pburl,
                                                  self._got_queen)

    def register_control(self):
        c = ControlServer()
        c.setServiceParent(self)
        control_url = self.tub.registerReference(c)
        f = open("control.pburl", "w")
        f.write(control_url + "\n")
        f.close()
        os.chmod("control.pburl", 0600)

    def stopService(self):
        if self.introducer_client:
            self.introducer_client.stop()
        return service.MultiService.stopService(self)

    def _got_queen(self, queen):
        self.log("connected to queen")
        d.addCallback(lambda x: queen.callRemote("get_global_vdrive"))
        d.addCallback(self._got_vdrive_root)

    def _got_vdrive_root(self, root):
        self.getServiceNamed("vdrive").set_root(root)
        if "webish" in self.namedServices:
            self.getServiceNamed("webish").set_root_dirnode(root)

    def remote_get_service(self, name):
        # TODO: 'vdrive' should not be public in the medium term
        return self.getServiceNamed(name)

    def get_remote_service(self, nodeid, servicename):
        if nodeid not in self.connections:
            return defer.fail(IndexError("no connection to that peer"))
        peer = self.connections[nodeid]
        d = peer.callRemote("get_service", name=servicename)
        return d


    def permute_peerids(self, key, max_count=None):
        # TODO: eventually reduce memory consumption by doing an insertion
        # sort of at most max_count elements
        results = []
        for nodeid in self.all_peers:
            assert isinstance(nodeid, str)
            permuted = sha.new(key + nodeid).digest()
            results.append((permuted, nodeid))
        results.sort()
        results = [r[1] for r in results]
        if max_count is None:
            return results
        return results[:max_count]
