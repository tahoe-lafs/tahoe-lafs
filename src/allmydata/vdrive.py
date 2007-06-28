
import os
from twisted.application import service
from zope.interface import implements
from allmydata.interfaces import IVirtualDrive
from allmydata import dirnode, uri
from twisted.internet import defer

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

    def startService(self):
        service.MultiService.startService(self)
        basedir = self.parent.basedir
        client = self.parent
        tub = self.parent.tub

        global_vdrive_furl = None
        furl_file = os.path.join(basedir, self.GLOBAL_VDRIVE_FURL_FILE)
        if os.path.exists(furl_file):
            f = open(furl_file, "r")
            global_vdrive_furl = f.read().strip()
            f.close()

        global_uri_file = os.path.join(basedir,
                                       self.GLOBAL_VDRIVE_URI_FILE)
        if os.path.exists(global_uri_file):
            f = open(global_uri_file)
            self._global_uri = f.read().strip()
            f.close()
        elif global_vdrive_furl:
            d = tub.getReference(global_vdrive_furl)
            d.addCallback(lambda vdrive_server:
                          vdrive_server.callRemote("get_public_root_uri"))
            def _got_global_uri(global_uri):
                self._global_uri = global_uri
            d.addCallback(_got_global_uri)

        private_uri_file = os.path.join(basedir,
                                        self.MY_VDRIVE_URI_FILE)
        if os.path.exists(private_uri_file):
            f = open(private_uri_file)
            private_vdrive_uri = f.read().strip()
            f.close()
        elif global_vdrive_furl:
            d = dirnode.create_directory(client, global_vdrive_furl)
            def _got_directory(dirnode):
                private_uri = dirnode.get_uri()
                self._private_uri = private_uri
                f = open(private_uri_file, "w")
                f.write(private_uri + "\n")
                f.close()
            d.addCallback(_got_directory)


    def get_node(self, node_uri):
        if uri.is_dirnode_uri(node_uri):
            return dirnode.create_directory_node(self.parent, node_uri)
        else:
            return defer.succeed(dirnode.FileNode(node_uri, self.parent))

    def have_public_root(self):
        return bool(self._global_uri)
    def get_public_root(self):
        if not self._global_uri:
            return defer.fail(NoGlobalVirtualDriveError())
        return self.get_node(self._global_uri)

    def have_private_root(self):
        return bool(self._private_uri)
    def get_private_root(self):
        if not self._private_uri:
            return defer.fail(NoPrivateVirtualDriveError())
        return self.get_node(self._private_uri)

