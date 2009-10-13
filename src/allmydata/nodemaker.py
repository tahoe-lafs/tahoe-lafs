import weakref
from allmydata.immutable.filenode import FileNode, LiteralFileNode
from allmydata.mutable.filenode import MutableFileNode
from allmydata.dirnode import DirectoryNode
from allmydata.unknown import UnknownNode
from allmydata.uri import DirectoryURI, ReadonlyDirectoryURI

# the "node maker" is a two-argument callable (really a 'create' method on a
# NodeMaker instance) which accepts a URI string (and an optional readcap
# string, for use by dirnode.copy) and returns an object which (at the very
# least) provides IFilesystemNode. That interface has other methods that can
# be used to determine if the node represents a file or directory, in which
# case other methods are available (like download() or modify()). Each Tahoe
# process will typically have a single NodeMaker, but unit tests may create
# simplified/mocked forms for test purposes.

# any authorities which fsnodes will need (like a reference to the
# StorageFarmBroker, to access storage servers for publish/retrieve/download)
# will be retained as attributes inside the NodeMaker and passed to fsnodes
# as necessary.

class NodeMaker:
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
        d = self.create_mutable_file()
        d.addCallback(self._create_dirnode)
        if initial_children:
            d.addCallback(lambda n: n.set_children(initial_children))
        return d
