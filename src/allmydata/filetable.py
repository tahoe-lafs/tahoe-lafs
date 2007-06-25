
import os
from zope.interface import implements
from twisted.application import service
from foolscap import Referenceable
from allmydata.interfaces import RIVirtualDriveServer
from allmydata.util import bencode, idlib, hashutil, fileutil
from allmydata import uri

class BadWriteEnablerError(Exception):
    pass
class ChildAlreadyPresentError(Exception):
    pass

class NoPublicRootError(Exception):
    pass

class VirtualDriveServer(service.MultiService, Referenceable):
    implements(RIVirtualDriveServer)
    name = "filetable"

    def __init__(self, basedir, offer_public_root=True):
        service.MultiService.__init__(self)
        self._basedir = os.path.abspath(basedir)
        fileutil.make_dirs(self._basedir)
        self._root = None
        if offer_public_root:
            rootfile = os.path.join(self._basedir, "root")
            if not os.path.exists(rootfile):
                write_key = hashutil.random_key()
                (wk, we, rk, index) = \
                     hashutil.generate_dirnode_keys_from_writekey(write_key)
                self.create_directory(index, we)
                f = open(rootfile, "wb")
                f.write(wk)
                f.close()
                self._root = wk
            else:
                f = open(rootfile, "rb")
                self._root = f.read()

    def set_furl(self, myfurl):
        self._myfurl = myfurl

    def get_public_root_uri(self):
        if self._root:
            return uri.pack_dirnode_uri(self._myfurl, self._root)
        raise NoPublicRootError
    remote_get_public_root_uri = get_public_root_uri

    def create_directory(self, index, write_enabler):
        data = [write_enabler, []]
        self._write_to_file(index, data)
        return index
    remote_create_directory = create_directory

    # the file on disk consists of the write_enabler token and a list of
    # (H(name), E(name), E(write), E(read)) tuples.

    def _read_from_file(self, index):
        name = idlib.b2a(index)
        data = open(os.path.join(self._basedir, name), "rb").read()
        return bencode.bdecode(data)

    def _write_to_file(self, index, data):
        name = idlib.b2a(index)
        f = open(os.path.join(self._basedir, name), "wb")
        f.write(bencode.bencode(data))
        f.close()


    def get(self, index, key):
        data = self._read_from_file(index)
        for (H_key, E_key, E_write, E_read) in data[1]:
            if H_key == key:
                return (E_write, E_read)
        raise IndexError("unable to find key %s" % idlib.b2a(key))
    remote_get = get

    def list(self, index):
        data = self._read_from_file(index)
        response = [ (E_key, E_write, E_read)
                     for (H_key, E_key, E_write, E_read) in data[1] ]
        return response
    remote_list = list

    def delete(self, index, write_enabler, key):
        data = self._read_from_file(index)
        if data[0] != write_enabler:
            raise BadWriteEnablerError
        for i,(H_key, E_key, E_write, E_read) in enumerate(data[1]):
            if H_key == key:
                del data[1][i]
                self._write_to_file(index, data)
                return
        raise IndexError("unable to find key %s" % idlib.b2a(key))
    remote_delete = delete

    def set(self, index, write_enabler, key,   name, write, read):
        data = self._read_from_file(index)
        if data[0] != write_enabler:
            raise BadWriteEnablerError
        # first, see if the key is already present
        for i,(H_key, E_key, E_write, E_read) in enumerate(data[1]):
            if H_key == key:
                raise ChildAlreadyPresentError
        # now just append the data
        data[1].append( (key, name, write, read) )
        self._write_to_file(index, data)
    remote_set = set
