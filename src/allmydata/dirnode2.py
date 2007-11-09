
import os

from zope.interface import implements
from twisted.internet import defer
import simplejson
from allmydata.interfaces import IMutableFileNode, IDirectoryNode,\
     INewDirectoryURI, IFileNode, NotMutableError, \
     IVerifierURI
from allmydata.util import hashutil
from allmydata.util.hashutil import netstring
from allmydata.dirnode import IntegrityCheckError
from allmydata.uri import NewDirectoryURI
from allmydata.Crypto.Cipher import AES

from allmydata.mutable import MutableFileNode

def split_netstring(data, numstrings, allow_leftover=False):
    """like string.split(), but extracts netstrings. If allow_leftover=False,
    returns numstrings elements, and throws ValueError if there was leftover
    data. If allow_leftover=True, returns numstrings+1 elements, in which the
    last element is the leftover data (possibly an empty string)"""
    elements = []
    assert numstrings >= 0
    while data:
        colon = data.index(":")
        length = int(data[:colon])
        string = data[colon+1:colon+1+length]
        assert len(string) == length
        elements.append(string)
        assert data[colon+1+length] == ","
        data = data[colon+1+length+1:]
        if len(elements) == numstrings:
            break
    if len(elements) < numstrings:
        raise ValueError("ran out of netstrings")
    if allow_leftover:
        return tuple(elements + [data])
    if data:
        raise ValueError("leftover data in netstrings")
    return tuple(elements)

class NewDirectoryNode:
    implements(IDirectoryNode)
    filenode_class = MutableFileNode

    def __init__(self, client):
        self._client = client
    def init_from_uri(self, myuri):
        u = INewDirectoryURI(myuri)
        self._uri = u
        self._node = self.filenode_class(self._client)
        self._node.init_from_uri(u.get_filenode_uri())
        return self

    def create(self):
        # first we create a MutableFileNode with empty_contents, then use its
        # URI to create our own.
        self._node = self.filenode_class(self._client)
        empty_contents = self._pack_contents({})
        d = self._node.create(empty_contents)
        d.addCallback(self._filenode_created)
        return d
    def _filenode_created(self, res):
        self._uri = NewDirectoryURI(self._node._uri)
        return None

    def _read(self):
        d = self._node.download_to_data()
        d.addCallback(self._unpack_contents)
        return d

    def _encrypt_rwcap(self, rwcap):
        assert isinstance(rwcap, str)
        IV = os.urandom(16)
        key = hashutil.mutable_rwcap_key_hash(IV, self._node.get_writekey())
        counterstart = "\x00"*16
        cryptor = AES.new(key=key, mode=AES.MODE_CTR, counterstart=counterstart)
        crypttext = cryptor.encrypt(rwcap)
        mac = hashutil.hmac(key, IV + crypttext)
        assert len(mac) == 32
        return IV + crypttext + mac

    def _decrypt_rwcapdata(self, encwrcap):
        IV = encwrcap[:16]
        crypttext = encwrcap[16:-32]
        mac = encwrcap[-32:]
        key = hashutil.mutable_rwcap_key_hash(IV, self._node.get_writekey())
        if mac != hashutil.hmac(key, IV+crypttext):
            raise IntegrityCheckError("HMAC does not match, crypttext is corrupted")
        counterstart = "\x00"*16
        cryptor = AES.new(key=key, mode=AES.MODE_CTR, counterstart=counterstart)
        plaintext = cryptor.decrypt(crypttext)
        return plaintext

    def _create_node(self, child_uri):
        return self._client.create_node_from_uri(child_uri)

    def _unpack_contents(self, data):
        # the directory is serialized as a list of netstrings, one per child.
        # Each child is serialized as a list of four netstrings: (name,
        # rocap, rwcap, metadata), in which the name,rocap,metadata are in
        # cleartext. The rwcap is formatted as:
        #  pack("16ss32s", iv, AES(H(writekey+iv), plaintextrwcap), mac)
        assert isinstance(data, str)
        # an empty directory is serialized as an empty string
        if data == "":
            return {}
        mutable = self.is_mutable()
        children = {}
        while len(data) > 0:
            entry, data = split_netstring(data, 1, True)
            name, rocap, rwcapdata, metadata_s = split_netstring(entry, 4)
            if mutable:
                rwcap = self._decrypt_rwcapdata(rwcapdata)
                child = self._create_node(rwcap)
            else:
                child = self._create_node(rocap)
            metadata = simplejson.loads(metadata_s)
            assert isinstance(metadata, dict)
            children[name] = (child, metadata)
        return children

    def _pack_contents(self, children):
        # expects children in the same format as _unpack_contents
        assert isinstance(children, dict)
        entries = []
        for name in sorted(children.keys()):
            child, metadata = children[name]
            assert (IFileNode.providedBy(child)
                    or IMutableFileNode.providedBy(child)
                    or IDirectoryNode.providedBy(child))
            assert isinstance(metadata, dict)
            rwcap = child.get_uri() # might be RO if the child is not mutable
            rocap = child.get_readonly()
            entry = "".join([netstring(name),
                             netstring(rocap),
                             netstring(self._encrypt_rwcap(rwcap)),
                             netstring(simplejson.dumps(metadata))])
            entries.append(netstring(entry))
        return "".join(entries)

    def is_readonly(self):
        return self._node.is_readonly()
    def is_mutable(self):
        return self._node.is_mutable()

    def get_uri(self):
        return self._uri.to_string()

    def get_readonly(self):
        return self._uri.get_readonly().to_string()

    def get_immutable_uri(self):
        return self._uri.get_readonly().to_string()

    def get_verifier(self):
        return self._uri.get_verifier().to_string()

    def check(self):
        """Perform a file check. See IChecker.check for details."""
        pass # TODO

    def list(self):
        """I return a Deferred that fires with a dictionary mapping child
        name to an IFileNode or IDirectoryNode."""
        return self._read()

    def has_child(self, name):
        """I return a Deferred that fires with a boolean, True if there
        exists a child of the given name, False if not."""
        d = self._read()
        d.addCallback(lambda children: children.has_key(name))
        return d

    def get(self, name):
        """I return a Deferred that fires with a specific named child node,
        either an IFileNode or an IDirectoryNode."""
        d = self._read()
        d.addCallback(lambda children: children[name][0])
        return d

    def get_metadata_for(self, name):
        d = self._read()
        d.addCallback(lambda children: children[name][1])
        return d

    def get_child_at_path(self, path):
        """Transform a child path into an IDirectoryNode or IFileNode.

        I perform a recursive series of 'get' operations to find the named
        descendant node. I return a Deferred that fires with the node, or
        errbacks with IndexError if the node could not be found.

        The path can be either a single string (slash-separated) or a list of
        path-name elements.
        """

        if not path:
            return defer.succeed(self)
        if isinstance(path, (str, unicode)):
            path = path.split("/")
        childname = path[0]
        remaining_path = path[1:]
        d = self.get(childname)
        if remaining_path:
            def _got(node):
                return node.get_child_at_path(remaining_path)
            d.addCallback(_got)
        return d

    def set_uri(self, name, child_uri, metadata={}):
        """I add a child (by URI) at the specific name. I return a Deferred
        that fires when the operation finishes. I will replace any existing
        child of the same name.

        The child_uri could be for a file, or for a directory (either
        read-write or read-only, using a URI that came from get_uri() ).

        If this directory node is read-only, the Deferred will errback with a
        NotMutableError."""
        return self.set_node(name, self._create_node(child_uri), metadata)

    def set_node(self, name, child, metadata={}):
        """I add a child at the specific name. I return a Deferred that fires
        when the operation finishes. This Deferred will fire with the child
        node that was just added. I will replace any existing child of the
        same name.

        If this directory node is read-only, the Deferred will errback with a
        NotMutableError."""
        if self.is_readonly():
            return defer.fail(NotMutableError())
        d = self._read()
        def _add(children):
            children[name] = (child, metadata)
            new_contents = self._pack_contents(children)
            return self._node.replace(new_contents)
        d.addCallback(_add)
        d.addCallback(lambda res: None)
        return d

    def add_file(self, name, uploadable):
        """I upload a file (using the given IUploadable), then attach the
        resulting FileNode to the directory at the given name. I return a
        Deferred that fires (with the IFileNode of the uploaded file) when
        the operation completes."""
        if self.is_readonly():
            return defer.fail(NotMutableError())
        d = self._client.upload(uploadable)
        d.addCallback(self._client.create_node_from_uri)
        d.addCallback(lambda node: self.set_node(name, node))
        return d

    def delete(self, name):
        """I remove the child at the specific name. I return a Deferred that
        fires (with the node just removed) when the operation finishes."""
        if self.is_readonly():
            return defer.fail(NotMutableError())
        d = self._read()
        def _delete(children):
            old_child, metadata = children[name]
            del children[name]
            new_contents = self._pack_contents(children)
            d = self._node.replace(new_contents)
            def _done(res):
                return old_child
            d.addCallback(_done)
            return d
        d.addCallback(_delete)
        return d

    def create_empty_directory(self, name):
        """I create and attach an empty directory at the given name. I return
        a Deferred that fires (with the new directory node) when the
        operation finishes."""
        if self.is_readonly():
            return defer.fail(NotMutableError())
        d = self._client.create_empty_dirnode()
        def _created(child):
            d = self.set_node(name, child)
            d.addCallback(lambda res: child)
            return d
        d.addCallback(_created)
        return d

    def move_child_to(self, current_child_name, new_parent,
                      new_child_name=None):
        """I take one of my children and move them to a new parent. The child
        is referenced by name. On the new parent, the child will live under
        'new_child_name', which defaults to 'current_child_name'. I return a
        Deferred that fires when the operation finishes."""
        if self.is_readonly() or new_parent.is_readonly():
            return defer.fail(NotMutableError())
        if new_child_name is None:
            new_child_name = current_child_name
        d = self.get(current_child_name)
        d.addCallback(lambda child: new_parent.set_node(new_child_name, child))
        d.addCallback(lambda child: self.delete(current_child_name))
        return d

    def build_manifest(self):
        """Return a frozenset of verifier-capability strings for all nodes
        (directories and files) reachable from this one."""

        # this is just a tree-walker, except that following each edge
        # requires a Deferred.

        manifest = set()
        manifest.add(self.get_verifier())

        d = self._build_manifest_from_node(self, manifest)
        def _done(res):
            # LIT nodes have no verifier-capability: their data is stored
            # inside the URI itself, so there is no need to refresh anything.
            # They indicate this by returning None from their get_verifier
            # method. We need to remove any such Nones from our set. We also
            # want to convert all these caps into strings.
            return frozenset([IVerifierURI(cap).to_string()
                              for cap in manifest
                              if cap is not None])
        d.addCallback(_done)
        return d

    def _build_manifest_from_node(self, node, manifest):
        d = node.list()
        def _got_list(res):
            dl = []
            for name, (child, metadata) in res.iteritems():
                verifier = child.get_verifier()
                if verifier not in manifest:
                    manifest.add(verifier)
                    if IDirectoryNode.providedBy(child):
                        dl.append(self._build_manifest_from_node(child,
                                                                 manifest))
            if dl:
                return defer.DeferredList(dl)
        d.addCallback(_got_list)
        return d

# use client.create_dirnode() to make one of these


