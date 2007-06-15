
import os
from zope.interface import implements
from foolscap import Referenceable
from allmydata.interfaces import RIVirtualDriveServer, RIMutableDirectoryNode
from allmydata.vdrive import FileNode, DirectoryNode
from allmydata.util import bencode, idlib
from twisted.application import service
from twisted.python import log

class BadNameError(Exception):
    """Bad filename component"""

class BadFileError(Exception):
    pass

class BadDirectoryError(Exception):
    pass

class MutableDirectoryNode(Referenceable):
    """I represent a single directory.

    I am associated with a file on disk, using a randomly-generated (and
    hopefully unique) name. This file contains a serialized dictionary which
    maps child names to 'child specifications'. These specifications are
    tuples, either of ('file', URI), or ('subdir', FURL).
    """

    implements(RIMutableDirectoryNode)

    def __init__(self, basedir, name=None):
        self._basedir = basedir
        if name:
            self._name = name
            # for well-known nodes, make sure they exist
            try:
                ignored = self._read_from_file()
            except EnvironmentError:
                self._write_to_file({})
        else:
            self._name = self.create_filename()
            self._write_to_file({}) # start out empty

    def _read_from_file(self):
        data = open(os.path.join(self._basedir, self._name), "rb").read()
        children = bencode.bdecode(data)
        child_nodes = {}
        for k,v in children.iteritems():
            if v[0] == "file":
                child_nodes[k] = FileNode(v[1])
            elif v[0] == "subdir":
                child_nodes[k] = DirectoryNode(v[1])
            else:
                raise RuntimeError("unknown child spec '%s'" % (v[0],))
        return child_nodes

    def _write_to_file(self, children):
        child_nodes = {}
        for k,v in children.iteritems():
            if isinstance(v, FileNode):
                child_nodes[k] = ("file", v.uri)
            elif isinstance(v, DirectoryNode):
                child_nodes[k] = ("subdir", v.furl)
            else:
                raise RuntimeError("unknown child[%s] node '%s'" % (k,v))
        data = bencode.bencode(child_nodes)
        f = open(os.path.join(self._basedir, self._name), "wb")
        f.write(data)
        f.close()


    def create_filename(self):
        return idlib.b2a(os.urandom(8))

    def validate_name(self, name):
        if name == "." or name == ".." or "/" in name:
            raise BadNameError("'%s' is not cool" % name)

    # these are the public methods, available to anyone who holds a reference

    def list(self):
        log.msg("Dir(%s).list()" % self._name)
        children = self._read_from_file()
        results = list(children.items())
        return sorted(results)
    remote_list = list

    def get(self, name):
        log.msg("Dir(%s).get(%s)" % (self._name, name))
        self.validate_name(name)
        children = self._read_from_file()
        if name not in children:
            raise BadFileError("no such child")
        return children[name]
    remote_get = get

    def add(self, name, child):
        self.validate_name(name)
        children = self._read_from_file()
        if name in children:
            raise BadNameError("the child already existed")
        children[name] = child
        self._write_to_file(children)
        return child
    remote_add = add

    def remove(self, name):
        self.validate_name(name)
        children = self._read_from_file()
        if name not in children:
            raise BadFileError("cannot remove non-existent child")
        child = children[name]
        del children[name]
        self._write_to_file(children)
        return child
    remote_remove = remove


class NoPublicRootError(Exception):
    pass

class VirtualDriveServer(service.MultiService, Referenceable):
    implements(RIVirtualDriveServer)
    name = "filetable"
    VDRIVEDIR = "vdrive"

    def __init__(self, basedir=".", offer_public_root=True):
        service.MultiService.__init__(self)
        vdrive_dir = os.path.join(basedir, self.VDRIVEDIR)
        if not os.path.exists(vdrive_dir):
            os.mkdir(vdrive_dir)
        self._vdrive_dir = vdrive_dir
        self._root = None
        if offer_public_root:
            self._root = MutableDirectoryNode(vdrive_dir, "root")

    def startService(self):
        service.MultiService.startService(self)
        # _register_all_dirnodes exists to avoid hacking our Tub to
        # automatically translate inbound your-reference names
        # (Tub.getReferenceForName) into MutableDirectoryNode instances by
        # looking in our basedir for them. Without that hack, we have to
        # register all nodes at startup to make sure they'll be available to
        # all callers. In addition, we must register any new ones that we
        # create later on.
        tub = self.parent.tub
        self._root_furl = tub.registerReference(self._root, "root")
        self._register_all_dirnodes(tub)

    def _register_all_dirnodes(self, tub):
        for name in os.listdir(self._vdrive_dir):
            node = MutableDirectoryNode(self._vdrive_dir, name)
            ignored_furl = tub.registerReference(node, name)

    def get_public_root_furl(self):
        if self._root:
            return self._root_furl
        raise NoPublicRootError
    remote_get_public_root_furl = get_public_root_furl

    def create_directory(self):
        node = MutableDirectoryNode(self._vdrive_dir)
        furl = self.parent.tub.registerReference(node, node._name)
        return DirectoryNode(furl)
    remote_create_directory = create_directory
