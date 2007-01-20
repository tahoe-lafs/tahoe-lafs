
import os, shutil
from zope.interface import implements
from foolscap import Referenceable
from allmydata.interfaces import RIMutableDirectoryNode
from twisted.application import service
from twisted.python import log

class DeadDirectoryNodeError(Exception):
    """The directory referenced by this node has been deleted."""

class BadDirectoryError(Exception):
    """There was a problem with the directory being referenced."""
class BadFileError(Exception):
    """The file being referenced does not exist."""
class BadNameError(Exception):
    """Bad filename component"""

class MutableDirectoryNode(Referenceable):
    implements(RIMutableDirectoryNode)

    def __init__(self, basedir):
        self._basedir = basedir

    def make_subnode(self, basedir):
        return self.__class__(basedir)

    def validate_name(self, name):
        if name == "." or name == ".." or "/" in name:
            raise BadNameError("'%s' is not cool" % name)

    # these are the public methods, available to anyone who holds a reference

    def list(self):
        log.msg("Dir(%s).list" % self._basedir)
        results = []
        if not os.path.isdir(self._basedir):
            raise DeadDirectoryNodeError("This directory has been deleted")
        for name in os.listdir(self._basedir):
            absname = os.path.join(self._basedir, name)
            if os.path.isdir(absname):
                results.append( (name, self.make_subnode(absname)) )
            elif os.path.isfile(absname):
                f = open(absname, "rb")
                data = f.read()
                f.close()
                results.append( (name, data) )
            # anything else is ignored
        return sorted(results)
    remote_list = list

    def get(self, name):
        self.validate_name(name)
        absname = os.path.join(self._basedir, name)
        if os.path.isdir(absname):
            return self.make_subnode(absname)
        elif os.path.isfile(absname):
            f = open(absname, "rb")
            data = f.read()
            f.close()
            return data
        else:
            raise BadFileError("there is nothing named '%s' in this directory"
                               % name)
    remote_get = get

    def add_directory(self, name):
        self.validate_name(name)
        absname = os.path.join(self._basedir, name)
        if os.path.isdir(absname):
            raise BadDirectoryError("the directory '%s' already exists" % name)
        if os.path.exists(absname):
            raise BadDirectoryError("the directory '%s' already exists "
                                    "(but isn't a directory)" % name)
        os.mkdir(absname)
        return self.make_subnode(absname)
    remote_add_directory = add_directory

    def add_file(self, name, uri):
        self.validate_name(name)
        f = open(os.path.join(self._basedir, name), "wb")
        f.write(uri)
        f.close()
    remote_add_file = add_file

    def remove(self, name):
        self.validate_name(name)
        absname = os.path.join(self._basedir, name)
        if os.path.isdir(absname):
            shutil.rmtree(absname)
        elif os.path.isfile(absname):
            os.unlink(absname)
        else:
            raise BadFileError("Cannot delete non-existent file '%s'" % name)
    remote_remove = remove


class GlobalVirtualDrive(service.MultiService):
    name = "filetable"
    VDRIVEDIR = "vdrive"

    def __init__(self, basedir="."):
        service.MultiService.__init__(self)
        vdrive_dir = os.path.join(basedir, self.VDRIVEDIR)
        if not os.path.exists(vdrive_dir):
            os.mkdir(vdrive_dir)
        self._root = MutableDirectoryNode(vdrive_dir)

    def get_root(self):
        return self._root

