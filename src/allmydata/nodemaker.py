import weakref
from zope.interface import implements
from allmydata.util.assertutil import precondition
from allmydata.interfaces import INodeMaker
from allmydata.immutable.filenode import FileNode, LiteralFileNode
from allmydata.mutable.filenode import MutableFileNode
from allmydata.dirnode import DirectoryNode, pack_children
from allmydata.unknown import UnknownNode
from allmydata.uri import DirectoryURI, ReadonlyDirectoryURI

class NodeMaker:
    implements(INodeMaker)

    def __init__(self, storage_broker, secret_holder, history,
                 uploader, downloader, download_cache_dirman,
                 default_encoding_parameters, key_generator):
        self.storage_broker = storage_broker
        self.secret_holder = secret_holder
        self.history = history
        self.uploader = uploader
        self.downloader = downloader
        self.download_cache_dirman = download_cache_dirman
        self.default_encoding_parameters = default_encoding_parameters
        self.key_generator = key_generator

        self._node_cache = weakref.WeakValueDictionary() # uri -> node

    def _create_lit(self, cap):
        return LiteralFileNode(cap)
    def _create_immutable(self, cap):
        return FileNode(cap, self.storage_broker, self.secret_holder,
                        self.downloader, self.history,
                        self.download_cache_dirman)
    def _create_mutable(self, cap):
        n = MutableFileNode(self.storage_broker, self.secret_holder,
                            self.default_encoding_parameters,
                            self.history)
        return n.init_from_uri(cap)
    def _create_dirnode(self, filenode):
        return DirectoryNode(filenode, self, self.uploader)

    def create_from_cap(self, writecap, readcap=None):
        # this returns synchronously.
        assert isinstance(writecap, (str, type(None))), type(writecap)
        assert isinstance(readcap,  (str, type(None))), type(readcap)
        cap = writecap or readcap
        if not cap:
            # maybe the writecap was hidden because we're in a readonly
            # directory, and the future cap format doesn't have a readcap, or
            # something.
            return UnknownNode(writecap, readcap)
        if cap in self._node_cache:
            return self._node_cache[cap]
        elif cap.startswith("URI:LIT:"):
            node = self._create_lit(cap)
        elif cap.startswith("URI:CHK:"):
            node = self._create_immutable(cap)
        elif cap.startswith("URI:SSK-RO:") or cap.startswith("URI:SSK:"):
            node = self._create_mutable(cap)
        elif cap.startswith("URI:DIR2-RO:") or cap.startswith("URI:DIR2:"):
            if cap.startswith("URI:DIR2-RO:"):
                dircap = ReadonlyDirectoryURI.init_from_string(cap)
            elif cap.startswith("URI:DIR2:"):
                dircap = DirectoryURI.init_from_string(cap)
            filecap = dircap.get_filenode_uri().to_string()
            filenode = self.create_from_cap(filecap)
            node = self._create_dirnode(filenode)
        else:
            return UnknownNode(writecap, readcap) # don't cache UnknownNode
        self._node_cache[cap] = node  # note: WeakValueDictionary
        return node


    def create_mutable_file(self, contents=None, keysize=None):
        n = MutableFileNode(self.storage_broker, self.secret_holder,
                            self.default_encoding_parameters, self.history)
        d = self.key_generator.generate(keysize)
        d.addCallback(n.create_with_keys, contents)
        d.addCallback(lambda res: n)
        return d

    def create_new_mutable_directory(self, initial_children={}):
        # initial_children must have metadata (i.e. {} instead of None), and
        # should not contain UnknownNodes
        for (name, (node, metadata)) in initial_children.iteritems():
            precondition(not isinstance(node, UnknownNode),
                         "create_new_mutable_directory does not accept UnknownNode", node)
            precondition(isinstance(metadata, dict),
                         "create_new_mutable_directory requires metadata to be a dict, not None", metadata)
        d = self.create_mutable_file(lambda n:
                                     pack_children(n, initial_children))
        d.addCallback(self._create_dirnode)
        return d
