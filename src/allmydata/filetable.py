
import os
from zope.interface import implements
from foolscap import Referenceable
from allmydata.interfaces import RIMutableDirectoryNode
from allmydata.util import bencode, idlib
from allmydata.util.assertutil import _assert
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
    tuples, either of ('file', URI), or ('subdir', nodename).
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

    def make_subnode(self, name=None):
        return self.__class__(self._basedir, name)

    def _read_from_file(self):
        f = open(os.path.join(self._basedir, self._name), "rb")
        data = f.read()
        f.close()
        children_specifications = bencode.bdecode(data)
        children = {}
        for k,v in children_specifications.items():
            nodetype = v[0]
            if nodetype == "file":
                (uri, ) = v[1:]
                child = uri
            elif nodetype == "subdir":
                (nodename, ) = v[1:]
                child = self.make_subnode(nodename)
            else:
                _assert("Unknown nodetype in node specification %s" % (v,))
            children[k] = child
        return children

    def _write_to_file(self, children):
        children_specifications = {}
        for k,v in children.items():
            if isinstance(v, MutableDirectoryNode):
                child = ("subdir", v._name)
            else:
                assert isinstance(v, str)
                child = ("file", v) # URI
            children_specifications[k] = child
        data = bencode.bencode(children_specifications)
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

    def add_directory(self, name):
        self.validate_name(name)
        children = self._read_from_file()
        if name in children:
            raise BadDirectoryError("the directory already existed")
        children[name] = child = self.make_subnode()
        self._write_to_file(children)
        return child
    remote_add_directory = add_directory

    def add_file(self, name, uri):
        self.validate_name(name)
        children = self._read_from_file()
        children[name] = uri
        self._write_to_file(children)
    remote_add_file = add_file

    def remove(self, name):
        self.validate_name(name)
        children = self._read_from_file()
        if name not in children:
            raise BadFileError("cannot remove non-existent child")
        dead_child = children[name]
        del children[name]
        self._write_to_file(children)
        #return dead_child
    remote_remove = remove


class GlobalVirtualDrive(service.MultiService):
    name = "filetable"
    VDRIVEDIR = "vdrive"

    def __init__(self, basedir="."):
        service.MultiService.__init__(self)
        vdrive_dir = os.path.join(basedir, self.VDRIVEDIR)
        if not os.path.exists(vdrive_dir):
            os.mkdir(vdrive_dir)
        self._root = MutableDirectoryNode(vdrive_dir, "root")

    def get_root(self):
        return self._root

