"""
Create file nodes of various types.
"""

from __future__ import annotations

import weakref
from zope.interface import implementer
from twisted.internet.defer import succeed
from allmydata.util.assertutil import precondition
from allmydata.interfaces import INodeMaker
from allmydata.immutable.literal import LiteralFileNode
from allmydata.immutable.filenode import ImmutableFileNode, CiphertextFileNode
from allmydata.immutable.upload import Data
from allmydata.mutable.filenode import MutableFileNode
from allmydata.mutable.publish import MutableData
from allmydata.dirnode import DirectoryNode, pack_children
from allmydata.unknown import UnknownNode
from allmydata.blacklist import ProhibitedNode
from allmydata.crypto.rsa import PublicKey, PrivateKey
from allmydata import uri


@implementer(INodeMaker)
class NodeMaker(object):

    def __init__(self, storage_broker, secret_holder, history,
                 uploader, terminator,
                 default_encoding_parameters, mutable_file_default,
                 key_generator, blacklist=None):
        self.storage_broker = storage_broker
        self.secret_holder = secret_holder
        self.history = history
        self.uploader = uploader
        self.terminator = terminator
        self.default_encoding_parameters = default_encoding_parameters
        self.mutable_file_default = mutable_file_default
        self.key_generator = key_generator
        self.blacklist = blacklist

        self._node_cache = weakref.WeakValueDictionary() # uri -> node

    def _create_lit(self, cap):
        return LiteralFileNode(cap)
    def _create_immutable(self, cap):
        return ImmutableFileNode(cap, self.storage_broker, self.secret_holder,
                                 self.terminator, self.history)
    def _create_immutable_verifier(self, cap):
        return CiphertextFileNode(cap, self.storage_broker, self.secret_holder,
                                  self.terminator, self.history)
    def _create_mutable(self, cap):
        n = MutableFileNode(self.storage_broker, self.secret_holder,
                            self.default_encoding_parameters,
                            self.history)
        return n.init_from_cap(cap)
    def _create_dirnode(self, filenode):
        return DirectoryNode(filenode, self, self.uploader)

    def create_from_cap(self, writecap, readcap=None, deep_immutable=False, name=u"<unknown name>"):
        # this returns synchronously. It starts with a "cap string".
        assert isinstance(writecap, (bytes, type(None))), type(writecap)
        assert isinstance(readcap,  (bytes, type(None))), type(readcap)

        bigcap = writecap or readcap
        if not bigcap:
            # maybe the writecap was hidden because we're in a readonly
            # directory, and the future cap format doesn't have a readcap, or
            # something.
            return UnknownNode(None, None)  # deep_immutable and name not needed

        # The name doesn't matter for caching since it's only used in the error
        # attribute of an UnknownNode, and we don't cache those.
        if deep_immutable:
            memokey = b"I" + bigcap
        else:
            memokey = b"M" + bigcap
        try:
            node = self._node_cache[memokey]
        except KeyError:
            cap = uri.from_string(bigcap, deep_immutable=deep_immutable,
                                  name=name)
            node = self._create_from_single_cap(cap)

            # node is None for an unknown URI, otherwise it is a type for which
            # is_mutable() is known. We avoid cacheing mutable nodes due to
            # ticket #1679.
            if node is None:
                # don't cache UnknownNode
                node = UnknownNode(writecap, readcap,
                                   deep_immutable=deep_immutable, name=name)
            elif node.is_mutable():
                self._node_cache[memokey] = node  # note: WeakValueDictionary

        if self.blacklist:
            si = node.get_storage_index()
            # if this node is blacklisted, return the reason, otherwise return None
            reason = self.blacklist.check_storageindex(si)
            if reason is not None:
                # The original node object is cached above, not the ProhibitedNode wrapper.
                # This ensures that removing the blacklist entry will make the node
                # accessible if create_from_cap is called again.
                node = ProhibitedNode(node, reason)
        return node

    def _create_from_single_cap(self, cap):
        if isinstance(cap, uri.LiteralFileURI):
            return self._create_lit(cap)
        if isinstance(cap, uri.CHKFileURI):
            return self._create_immutable(cap)
        if isinstance(cap, uri.CHKFileVerifierURI):
            return self._create_immutable_verifier(cap)
        if isinstance(cap, (uri.ReadonlySSKFileURI, uri.WriteableSSKFileURI,
                            uri.WriteableMDMFFileURI, uri.ReadonlyMDMFFileURI)):
            return self._create_mutable(cap)
        if isinstance(cap, (uri.DirectoryURI,
                            uri.ReadonlyDirectoryURI,
                            uri.ImmutableDirectoryURI,
                            uri.LiteralDirectoryURI,
                            uri.MDMFDirectoryURI,
                            uri.ReadonlyMDMFDirectoryURI)):
            filenode = self._create_from_single_cap(cap.get_filenode_cap())
            return self._create_dirnode(filenode)
        return None

    def create_mutable_file(self, contents=None, version=None, keypair: tuple[PublicKey, PrivateKey] | None = None):
        if version is None:
            version = self.mutable_file_default
        n = MutableFileNode(self.storage_broker, self.secret_holder,
                            self.default_encoding_parameters, self.history)
        if keypair is None:
            d = self.key_generator.generate()
        else:
            d = succeed(keypair)
        d.addCallback(n.create_with_keys, contents, version=version)
        d.addCallback(lambda res: n)
        return d

    def create_new_mutable_directory(self, initial_children=None, version=None):
        if initial_children is None:
            initial_children = {}
        for (name, (node, metadata)) in initial_children.items():
            precondition(isinstance(metadata, dict),
                         "create_new_mutable_directory requires metadata to be a dict, not None", metadata)
            node.raise_error()
        d = self.create_mutable_file(lambda n:
                                     MutableData(pack_children(initial_children,
                                                    n.get_writekey())),
                                     version=version)
        d.addCallback(self._create_dirnode)
        return d

    def create_immutable_directory(self, children, convergence=None):
        if convergence is None:
            convergence = self.secret_holder.get_convergence_secret()
        packed = pack_children(children, None, deep_immutable=True)
        uploadable = Data(packed, convergence)
        # XXX should pass reactor arg
        d = self.uploader.upload(uploadable)
        d.addCallback(lambda results:
                      self.create_from_cap(None, results.get_uri()))
        d.addCallback(self._create_dirnode)
        return d
