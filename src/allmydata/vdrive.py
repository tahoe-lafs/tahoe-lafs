
"""This is the client-side facility to manipulate virtual drives."""

import os.path
from zope.interface import implements
from twisted.internet import defer
from allmydata import uri
from allmydata.Crypto.Cipher import AES
from allmydata.util import hashutil, idlib
from allmydata.interfaces import IDirectoryNode, IFileNode

class NotMutableError(Exception):
    pass


def create_directory_node(client, diruri):
    assert uri.is_dirnode_uri(diruri)
    if uri.is_mutable_dirnode_uri(diruri):
        dirnode_class = MutableDirectoryNode
    else:
        dirnode_class = ImmutableDirectoryNode
    (furl, key) = uri.unpack_dirnode_uri(diruri)
    d = client.tub.getReference(furl)
    def _got(rref):
        dirnode = dirnode_class(diruri, client, rref, key)
        return dirnode
    d.addCallback(_got)
    return d

IV_LENGTH = 14
def encrypt(key, data):
    IV = os.urandom(IV_LENGTH)
    counterstart = IV + "\x00"*(16-IV_LENGTH)
    assert len(counterstart) == 16, len(counterstart)
    cryptor = AES.new(key=key, mode=AES.MODE_CTR, counterstart=counterstart)
    crypttext = cryptor.encrypt(data)
    mac = hashutil.hmac(key, IV + crypttext)
    assert len(mac) == 32
    return IV + crypttext + mac

class IntegrityCheckError(Exception):
    pass

def decrypt(key, data):
    assert len(data) >= (32+IV_LENGTH), len(data)
    IV, crypttext, mac = data[:IV_LENGTH], data[IV_LENGTH:-32], data[-32:]
    if mac != hashutil.hmac(key, IV+crypttext):
        raise IntegrityCheckError("HMAC does not match, crypttext is corrupted")
    counterstart = IV + "\x00"*(16-IV_LENGTH)
    assert len(counterstart) == 16, len(counterstart)
    cryptor = AES.new(key=key, mode=AES.MODE_CTR, counterstart=counterstart)
    plaintext = cryptor.decrypt(crypttext)
    return plaintext


class ImmutableDirectoryNode:
    implements(IDirectoryNode)

    def __init__(self, myuri, client, rref, readkey):
        self._uri = myuri
        self._client = client
        self._tub = client.tub
        self._rref = rref
        self._readkey = readkey
        self._writekey = None
        self._write_enabler = None
        self._index = hashutil.dir_index_hash(self._readkey)
        self._mutable = False

    def dump(self):
        return ["URI: %s" % self._uri,
                "rk: %s" % idlib.b2a(self._readkey),
                "index: %s" % idlib.b2a(self._index),
                ]

    def is_mutable(self):
        return self._mutable

    def get_uri(self):
        return self._uri

    def get_immutable_uri(self):
        # return the dirnode URI for a read-only form of this directory
        if self._mutable:
            return uri.make_immutable_dirnode_uri(self._uri)
        else:
            return self._uri

    def __hash__(self):
        return hash((self.__class__, self._uri))
    def __cmp__(self, them):
        if cmp(type(self), type(them)):
            return cmp(type(self), type(them))
        if cmp(self.__class__, them.__class__):
            return cmp(self.__class__, them.__class__)
        return cmp(self._uri, them._uri)

    def _encrypt(self, key, data):
        return encrypt(key, data)

    def _decrypt(self, key, data):
        return decrypt(key, data)

    def _decrypt_child(self, E_write, E_read):
        if E_write and self._writekey:
            # we prefer read-write children when we can get them
            return self._decrypt(self._writekey, E_write)
        else:
            return self._decrypt(self._readkey, E_read)

    def list(self):
        d = self._rref.callRemote("list", self._index)
        entries = {}
        def _got(res):
            dl = []
            for (E_name, E_write, E_read) in res:
                name = self._decrypt(self._readkey, E_name)
                child_uri = self._decrypt_child(E_write, E_read)
                d2 = self._create_node(child_uri)
                def _created(node, name):
                    entries[name] = node
                d2.addCallback(_created, name)
                dl.append(d2)
            return defer.DeferredList(dl)
        d.addCallback(_got)
        d.addCallback(lambda res: entries)
        return d

    def _hash_name(self, name):
        return hashutil.dir_name_hash(self._readkey, name)

    def get(self, name):
        H_name = self._hash_name(name)
        d = self._rref.callRemote("get", self._index, H_name)
        def _check_index_error(f):
            f.trap(IndexError)
            raise IndexError("get(index=%s): unable to find child named '%s'"
                             % (idlib.b2a(self._index), name))
        d.addErrback(_check_index_error)
        d.addCallback(lambda (E_write, E_read):
                      self._decrypt_child(E_write, E_read))
        d.addCallback(self._create_node)
        return d

    def _set(self, name, write_child, read_child):
        if not self._mutable:
            return defer.fail(NotMutableError())
        H_name = self._hash_name(name)
        E_name = self._encrypt(self._readkey, name)
        E_write = ""
        if self._writekey and write_child:
            E_write = self._encrypt(self._writekey, write_child)
        E_read = self._encrypt(self._readkey, read_child)
        d = self._rref.callRemote("set", self._index, self._write_enabler,
                                  H_name, E_name, E_write, E_read)
        return d

    def set_uri(self, name, child_uri):
        write, read = self._split_uri(child_uri)
        return self._set(name, write, read)

    def set_node(self, name, child):
        d = self.set_uri(name, child.get_uri())
        d.addCallback(lambda res: child)
        return d

    def delete(self, name):
        if not self._mutable:
            return defer.fail(NotMutableError())
        H_name = self._hash_name(name)
        d = self._rref.callRemote("delete", self._index, self._write_enabler,
                                  H_name)
        return d

    def _create_node(self, child_uri):
        if uri.is_dirnode_uri(child_uri):
            return create_directory_node(self._client, child_uri)
        else:
            return defer.succeed(FileNode(child_uri, self._client))

    def _split_uri(self, child_uri):
        if uri.is_dirnode_uri(child_uri):
            if uri.is_mutable_dirnode_uri(child_uri):
                write = child_uri
                read = uri.make_immutable_dirnode_uri(child_uri)
            else:
                write = None
                read = child_uri
            return (write, read)
        return (None, child_uri) # file

    def create_empty_directory(self, name):
        if not self._mutable:
            return defer.fail(NotMutableError())
        child_writekey = hashutil.random_key()
        my_furl, parent_writekey = uri.unpack_dirnode_uri(self._uri)
        child_uri = uri.pack_dirnode_uri(my_furl, child_writekey)
        child = MutableDirectoryNode(child_uri, self._client, self._rref,
                                     child_writekey)
        d = self._rref.callRemote("create_directory",
                                  child._index, child._write_enabler)
        d.addCallback(lambda index: self.set_node(name, child))
        return d

    def add_file(self, name, uploadable):
        if not self._mutable:
            return defer.fail(NotMutableError())
        uploader = self._client.getServiceNamed("uploader")
        d = uploader.upload(uploadable)
        d.addCallback(lambda uri: self.set_node(name,
                                                FileNode(uri, self._client)))
        return d

    def move_child_to(self, current_child_name,
                      new_parent, new_child_name=None):
        if not (self._mutable and new_parent.is_mutable()):
            return defer.fail(NotMutableError())
        if new_child_name is None:
            new_child_name = current_child_name
        d = self.get(current_child_name)
        d.addCallback(lambda child: new_parent.set_node(new_child_name, child))
        d.addCallback(lambda child: self.delete(current_child_name))
        return d

class MutableDirectoryNode(ImmutableDirectoryNode):
    implements(IDirectoryNode)

    def __init__(self, myuri, client, rref, writekey):
        readkey = hashutil.dir_read_key_hash(writekey)
        ImmutableDirectoryNode.__init__(self, myuri, client, rref, readkey)
        self._writekey = writekey
        self._write_enabler = hashutil.dir_write_enabler_hash(writekey)
        self._mutable = True

def create_directory(client, furl):
    write_key = hashutil.random_key()
    (wk, we, rk, index) = \
         hashutil.generate_dirnode_keys_from_writekey(write_key)
    myuri = uri.pack_dirnode_uri(furl, wk)
    d = client.tub.getReference(furl)
    def _got_vdrive_server(vdrive_server):
        node = MutableDirectoryNode(myuri, client, vdrive_server, wk)
        d2 = vdrive_server.callRemote("create_directory", index, we)
        d2.addCallback(lambda res: node)
        return d2
    d.addCallback(_got_vdrive_server)
    return d

class FileNode:
    implements(IFileNode)

    def __init__(self, uri, client):
        self.uri = uri
        self._client = client

    def get_uri(self):
        return self.uri

    def __hash__(self):
        return hash((self.__class__, self.uri))
    def __cmp__(self, them):
        if cmp(type(self), type(them)):
            return cmp(type(self), type(them))
        if cmp(self.__class__, them.__class__):
            return cmp(self.__class__, them.__class__)
        return cmp(self.uri, them.uri)

    def download(self, target):
        downloader = self._client.getServiceNamed("downloader")
        return downloader.download(self.uri, target)

    def download_to_data(self):
        downloader = self._client.getServiceNamed("downloader")
        return downloader.download_to_data(self.uri)

