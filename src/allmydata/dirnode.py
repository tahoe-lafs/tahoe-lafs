
import os

from zope.interface import implements
from twisted.internet import defer
import simplejson
from allmydata.mutable import NotMutableError
from allmydata.interfaces import IMutableFileNode, IDirectoryNode,\
     IURI, IFileNode, IMutableFileURI, IVerifierURI, IFilesystemNode
from allmydata.util import hashutil
from allmydata.util.hashutil import netstring
from allmydata.uri import NewDirectoryURI
from pycryptopp.cipher.aes import AES

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
    def __repr__(self):
        return "<%s %s %s>" % (self.__class__.__name__, self.is_readonly() and "RO" or "RW", hasattr(self, '_uri') and self._uri.abbrev())
    def init_from_uri(self, myuri):
        self._uri = IURI(myuri)
        self._node = self.filenode_class(self._client)
        self._node.init_from_uri(self._uri.get_filenode_uri())
        return self

    def create(self, wait_for_numpeers=None):
        """
        Returns a deferred that eventually fires with self once the directory
        has been created (distributed across a set of storage servers).
        """
        # first we create a MutableFileNode with empty_contents, then use its
        # URI to create our own.
        self._node = self.filenode_class(self._client)
        empty_contents = self._pack_contents({})
        d = self._node.create(empty_contents, wait_for_numpeers=wait_for_numpeers)
        d.addCallback(self._filenode_created)
        return d
    def _filenode_created(self, res):
        self._uri = NewDirectoryURI(IMutableFileURI(self._node.get_uri()))
        return self

    def _read(self):
        d = self._node.download_to_data()
        d.addCallback(self._unpack_contents)
        return d

    def _encrypt_rwcap(self, rwcap):
        assert isinstance(rwcap, str)
        IV = os.urandom(16)
        key = hashutil.mutable_rwcap_key_hash(IV, self._node.get_writekey())
        cryptor = AES(key)
        crypttext = cryptor.process(rwcap)
        mac = hashutil.hmac(key, IV + crypttext)
        assert len(mac) == 32
        return IV + crypttext + mac

    def _decrypt_rwcapdata(self, encwrcap):
        IV = encwrcap[:16]
        crypttext = encwrcap[16:-32]
        mac = encwrcap[-32:]
        key = hashutil.mutable_rwcap_key_hash(IV, self._node.get_writekey())
        if mac != hashutil.hmac(key, IV+crypttext):
            raise hashutil.IntegrityCheckError("HMAC does not match, crypttext is corrupted")
        cryptor = AES(key)
        plaintext = cryptor.process(crypttext)
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
        writeable = not self.is_readonly()
        children = {}
        while len(data) > 0:
            entry, data = split_netstring(data, 1, True)
            name, rocap, rwcapdata, metadata_s = split_netstring(entry, 4)
            if writeable:
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
                    or IDirectoryNode.providedBy(child)), (name,child)
            assert isinstance(metadata, dict)
            rwcap = child.get_uri() # might be RO if the child is not writeable
            rocap = child.get_readonly_uri()
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

    def get_readonly_uri(self):
        return self._uri.get_readonly().to_string()

    def get_verifier(self):
        return self._uri.get_verifier().to_string()

    def check(self):
        """Perform a file check. See IChecker.check for details."""
        return defer.succeed(None) # TODO

    def list(self):
        """I return a Deferred that fires with a dictionary mapping child
        name to a tuple of (IFileNode or IDirectoryNode, metadata)."""
        return self._read()

    def has_child(self, name):
        """I return a Deferred that fires with a boolean, True if there
        exists a child of the given name, False if not."""
        d = self._read()
        d.addCallback(lambda children: children.has_key(name))
        return d

    def _get(self, children, name):
        child = children.get(name)
        if child is None:
            raise KeyError(name)
        return child[0]

    def get(self, name):
        """I return a Deferred that fires with the named child node,
        which is either an IFileNode or an IDirectoryNode."""
        d = self._read()
        d.addCallback(self._get, name)
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

    def set_uri(self, name, child_uri, metadata={}, wait_for_numpeers=None):
        """I add a child (by URI) at the specific name. I return a Deferred
        that fires with the child node when the operation finishes. I will
        replace any existing child of the same name.

        The child_uri could be for a file, or for a directory (either
        read-write or read-only, using a URI that came from get_uri() ).

        If this directory node is read-only, the Deferred will errback with a
        NotMutableError."""
        return self.set_node(name, self._create_node(child_uri), metadata,
                             wait_for_numpeers)

    def set_uris(self, entries, wait_for_numpeers=None):
        node_entries = []
        for e in entries:
            if len(e) == 2:
                name, child_uri = e
                metadata = {}
            else:
                assert len(e) == 3
                name, child_uri, metadata = e
            node_entries.append( (name,self._create_node(child_uri),metadata) )
        return self.set_nodes(node_entries, wait_for_numpeers)

    def set_node(self, name, child, metadata={}, wait_for_numpeers=None):
        """I add a child at the specific name. I return a Deferred that fires
        when the operation finishes. This Deferred will fire with the child
        node that was just added. I will replace any existing child of the
        same name.

        If this directory node is read-only, the Deferred will errback with a
        NotMutableError."""
        assert IFilesystemNode.providedBy(child), child
        d = self.set_nodes( [(name, child, metadata)], wait_for_numpeers)
        d.addCallback(lambda res: child)
        return d

    def set_nodes(self, entries, wait_for_numpeers=None):
        if self.is_readonly():
            return defer.fail(NotMutableError())
        d = self._read()
        def _add(children):
            for e in entries:
                if len(e) == 2:
                    name, child = e
                    metadata = {}
                else:
                    assert len(e) == 3
                    name, child, metadata = e
                children[name] = (child, metadata)
            new_contents = self._pack_contents(children)
            return self._node.replace(new_contents, wait_for_numpeers=wait_for_numpeers)
        d.addCallback(_add)
        d.addCallback(lambda res: None)
        return d


    def add_file(self, name, uploadable, wait_for_numpeers=None):
        """I upload a file (using the given IUploadable), then attach the
        resulting FileNode to the directory at the given name. I return a
        Deferred that fires (with the IFileNode of the uploaded file) when
        the operation completes."""
        if self.is_readonly():
            return defer.fail(NotMutableError())
        d = self._client.upload(uploadable, wait_for_numpeers=wait_for_numpeers)
        d.addCallback(self._client.create_node_from_uri)
        d.addCallback(lambda node: self.set_node(name, node, wait_for_numpeers=wait_for_numpeers))
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

    def create_empty_directory(self, name, wait_for_numpeers=None):
        """I create and attach an empty directory at the given name. I return
        a Deferred that fires (with the new directory node) when the
        operation finishes."""
        if self.is_readonly():
            return defer.fail(NotMutableError())
        d = self._client.create_empty_dirnode(wait_for_numpeers=wait_for_numpeers)
        def _created(child):
            d = self.set_node(name, child, wait_for_numpeers=wait_for_numpeers)
            d.addCallback(lambda res: child)
            return d
        d.addCallback(_created)
        return d

    def move_child_to(self, current_child_name, new_parent,
                      new_child_name=None, wait_for_numpeers=None):
        """I take one of my children and move them to a new parent. The child
        is referenced by name. On the new parent, the child will live under
        'new_child_name', which defaults to 'current_child_name'. I return a
        Deferred that fires when the operation finishes."""
        if self.is_readonly() or new_parent.is_readonly():
            return defer.fail(NotMutableError())
        if new_child_name is None:
            new_child_name = current_child_name
        d = self.get(current_child_name)
        def sn(child):
            return new_parent.set_node(new_child_name, child,
                                wait_for_numpeers=wait_for_numpeers)
        d.addCallback(sn)
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


