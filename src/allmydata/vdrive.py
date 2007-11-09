
import os
from zope.interface import implements
from twisted.python import log
from twisted.application import service
from twisted.internet import defer
from allmydata.interfaces import IVirtualDrive, IDirnodeURI, IURI
from allmydata.util import observer
from allmydata import dirnode
from allmydata.dirnode2 import INewDirectoryURI

class NoGlobalVirtualDriveError(Exception):
    pass
class NoPrivateVirtualDriveError(Exception):
    pass

class VirtualDrive(service.MultiService):
    implements(IVirtualDrive)
    name = "vdrive"

    GLOBAL_VDRIVE_FURL_FILE = "vdrive.furl"

    GLOBAL_VDRIVE_URI_FILE = "global_root.uri"
    MY_VDRIVE_URI_FILE = "my_vdrive.uri"

    def __init__(self):
        service.MultiService.__init__(self)
        self._global_uri = None
        self._private_uri = None
        self._private_root_observer = observer.OneShotObserverList()

    def log(self, msg):
        self.parent.log(msg)

    def startService(self):
        service.MultiService.startService(self)
        basedir = self.parent.basedir
        tub = self.parent.tub

        global_uri_file = os.path.join(basedir, self.GLOBAL_VDRIVE_URI_FILE)
        if os.path.exists(global_uri_file):
            f = open(global_uri_file)
            self._global_uri = f.read().strip()
            f.close()
            self.log("using global vdrive uri %s" % self._global_uri)

        private_uri_file = os.path.join(basedir, self.MY_VDRIVE_URI_FILE)
        if os.path.exists(private_uri_file):
            f = open(private_uri_file)
            self._private_uri = f.read().strip()
            f.close()
            self.log("using private vdrive uri %s" % self._private_uri)
            self._private_root_observer.fire(self._private_uri)

        furl_file = os.path.join(basedir, self.GLOBAL_VDRIVE_FURL_FILE)
        if os.path.exists(furl_file):
            f = open(furl_file, "r")
            global_vdrive_furl = f.read().strip()
            f.close()
        else:
            self.log("no %s, cannot access global or private dirnodes"
                     % furl_file)
            return

        self.global_vdrive_furl = global_vdrive_furl
        tub.connectTo(global_vdrive_furl,
                      self._got_vdrive_server, global_vdrive_furl)

        if not self._global_uri:
            self.log("will fetch global_uri when we attach to the "
                     "vdrive server")

        if not self._private_uri:
            self.log("will create private_uri when we attach to the "
                     "vdrive server")


    def _got_vdrive_server(self, vdrive_server, global_vdrive_furl):
        self.log("connected to vdrive server")
        basedir = self.parent.basedir
        global_uri_file = os.path.join(basedir, self.GLOBAL_VDRIVE_URI_FILE)
        private_uri_file = os.path.join(basedir, self.MY_VDRIVE_URI_FILE)

        d = vdrive_server.callRemote("get_public_root_uri")
        def _got_global_uri(global_uri):
            self.log("got global_uri: %s" % global_uri)
            self._global_uri = global_uri
            f = open(global_uri_file, "w")
            f.write(self._global_uri + "\n")
            f.close()
        d.addCallback(_got_global_uri)

        if not self._private_uri:
            d.addCallback(lambda res:
                          dirnode.create_directory(self.parent,
                                                   global_vdrive_furl))
            def _got_directory(dirnode):
                self.log("creating private uri")
                self._private_uri = dirnode.get_uri()
                f = open(private_uri_file, "w")
                f.write(self._private_uri + "\n")
                f.close()
                self._private_root_observer.fire(self._private_uri)
            d.addCallback(_got_directory)

        def _oops(f):
            self.log("error getting URIs from vdrive server")
            log.err(f)
        d.addErrback(_oops)


    def have_public_root(self):
        return bool(self._global_uri)
    def get_public_root(self):
        if not self._global_uri:
            return defer.fail(NoGlobalVirtualDriveError())
        return self.get_node(self._global_uri)

    def when_private_root_available(self):
        """Return a Deferred that will fire with the URI of the private
        vdrive root, when it is available.

        This might be right away if the private vdrive was already present.
        The first time the node is started, this will take a bit longer.
        """
        return self._private_root_observer.when_fired()

    def have_private_root(self):
        return bool(self._private_uri)
    def get_private_root(self):
        if not self._private_uri:
            return defer.fail(NoPrivateVirtualDriveError())
        return self.get_node(self._private_uri)

    def get_node(self, node_uri):
        node_uri = IURI(node_uri)
        if (IDirnodeURI.providedBy(node_uri)
            and not INewDirectoryURI.providedBy(node_uri)):
            return dirnode.create_directory_node(self.parent, node_uri)
        else:
            return defer.succeed(self.parent.create_node_from_uri(node_uri))


    def get_node_at_path(self, path, root=None):
        if not isinstance(path, (list, tuple)):
            assert isinstance(path, (str, unicode))
            if path[0] == "/":
                path = path[1:]
            path = path.split("/")
        assert isinstance(path, (list, tuple))

        if root is None:
            if path and path[0] == "~":
                d = self.get_private_root()
                d.addCallback(lambda node:
                              self.get_node_at_path(path[1:], node))
                return d
            d = self.get_public_root()
            d.addCallback(lambda node: self.get_node_at_path(path, node))
            return d

        if path:
            assert path[0] != ""
            d = root.get(path[0])
            d.addCallback(lambda node: self.get_node_at_path(path[1:], node))
            return d

        return root

    def create_directory(self):
        # return a new+empty+unlinked dirnode
        assert self.global_vdrive_furl
        d = dirnode.create_directory(self.parent, self.global_vdrive_furl)
        return d
