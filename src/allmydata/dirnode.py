
import os, time, math

from zope.interface import implements
from twisted.internet import defer
import simplejson
from allmydata.mutable.common import NotMutableError
from allmydata.mutable.node import MutableFileNode
from allmydata.interfaces import IMutableFileNode, IDirectoryNode,\
     IURI, IFileNode, IMutableFileURI, IFilesystemNode, \
     ExistingChildError, NoSuchChildError, ICheckable, IDeepCheckable
from allmydata.checker_results import DeepCheckResults, \
     DeepCheckAndRepairResults
from allmydata.monitor import Monitor
from allmydata.util import hashutil, mathutil, base32, log
from allmydata.util.hashutil import netstring
from allmydata.util.limiter import ConcurrencyLimiter
from allmydata.util.netstring import split_netstring
from allmydata.uri import NewDirectoryURI
from pycryptopp.cipher.aes import AES

class Deleter:
    def __init__(self, node, name, must_exist=True):
        self.node = node
        self.name = name
        self.must_exist = True
    def modify(self, old_contents):
        children = self.node._unpack_contents(old_contents)
        if self.name not in children:
            if self.must_exist:
                raise NoSuchChildError(self.name)
            self.old_child = None
            return None
        self.old_child, metadata = children[self.name]
        del children[self.name]
        new_contents = self.node._pack_contents(children)
        return new_contents

class MetadataSetter:
    def __init__(self, node, name, metadata):
        self.node = node
        self.name = name
        self.metadata = metadata

    def modify(self, old_contents):
        children = self.node._unpack_contents(old_contents)
        if self.name not in children:
            raise NoSuchChildError(self.name)
        children[self.name] = (children[self.name][0], self.metadata)
        new_contents = self.node._pack_contents(children)
        return new_contents


class Adder:
    def __init__(self, node, entries=None, overwrite=True):
        self.node = node
        if entries is None:
            entries = []
        self.entries = entries
        self.overwrite = overwrite

    def set_node(self, name, node, metadata):
        self.entries.append( [name, node, metadata] )

    def modify(self, old_contents):
        children = self.node._unpack_contents(old_contents)
        now = time.time()
        for e in self.entries:
            if len(e) == 2:
                name, child = e
                new_metadata = None
            else:
                assert len(e) == 3
                name, child, new_metadata = e
            assert isinstance(name, unicode)
            if name in children:
                if not self.overwrite:
                    raise ExistingChildError("child '%s' already exists" % name)
                metadata = children[name][1].copy()
            else:
                metadata = {"ctime": now,
                            "mtime": now}
            if new_metadata is None:
                # update timestamps
                if "ctime" not in metadata:
                    metadata["ctime"] = now
                metadata["mtime"] = now
            else:
                # just replace it
                metadata = new_metadata.copy()
            children[name] = (child, metadata)
        new_contents = self.node._pack_contents(children)
        return new_contents

class NewDirectoryNode:
    implements(IDirectoryNode, ICheckable, IDeepCheckable)
    filenode_class = MutableFileNode

    def __init__(self, client):
        self._client = client
        self._most_recent_size = None

    def __repr__(self):
        return "<%s %s %s>" % (self.__class__.__name__, self.is_readonly() and "RO" or "RW", hasattr(self, '_uri') and self._uri.abbrev())
    def init_from_uri(self, myuri):
        self._uri = IURI(myuri)
        self._node = self.filenode_class(self._client)
        self._node.init_from_uri(self._uri.get_filenode_uri())
        return self

    def create(self, keypair_generator=None):
        """
        Returns a deferred that eventually fires with self once the directory
        has been created (distributed across a set of storage servers).
        """
        # first we create a MutableFileNode with empty_contents, then use its
        # URI to create our own.
        self._node = self.filenode_class(self._client)
        empty_contents = self._pack_contents({})
        d = self._node.create(empty_contents, keypair_generator)
        d.addCallback(self._filenode_created)
        return d
    def _filenode_created(self, res):
        self._uri = NewDirectoryURI(IMutableFileURI(self._node.get_uri()))
        return self

    def get_size(self):
        # return the size of our backing mutable file, in bytes, if we've
        # fetched it.
        return self._most_recent_size

    def _set_size(self, data):
        self._most_recent_size = len(data)
        return data

    def _read(self):
        d = self._node.download_best_version()
        d.addCallback(self._set_size)
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
        # cleartext. The 'name' is UTF-8 encoded. The rwcap is formatted as:
        # pack("16ss32s", iv, AES(H(writekey+iv), plaintextrwcap), mac)
        assert isinstance(data, str)
        # an empty directory is serialized as an empty string
        if data == "":
            return {}
        writeable = not self.is_readonly()
        children = {}
        while len(data) > 0:
            entry, data = split_netstring(data, 1, True)
            name, rocap, rwcapdata, metadata_s = split_netstring(entry, 4)
            name = name.decode("utf-8")
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
            assert isinstance(name, unicode)
            assert (IFileNode.providedBy(child)
                    or IMutableFileNode.providedBy(child)
                    or IDirectoryNode.providedBy(child)), (name,child)
            assert isinstance(metadata, dict)
            rwcap = child.get_uri() # might be RO if the child is not writeable
            rocap = child.get_readonly_uri()
            entry = "".join([netstring(name.encode("utf-8")),
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
        return self._uri.get_verifier()

    def get_storage_index(self):
        return self._uri._filenode_uri.storage_index

    def check(self, monitor, verify=False):
        """Perform a file check. See IChecker.check for details."""
        return self._node.check(monitor, verify)
    def check_and_repair(self, monitor, verify=False):
        return self._node.check_and_repair(monitor, verify)

    def list(self):
        """I return a Deferred that fires with a dictionary mapping child
        name to a tuple of (IFileNode or IDirectoryNode, metadata)."""
        return self._read()

    def has_child(self, name):
        """I return a Deferred that fires with a boolean, True if there
        exists a child of the given name, False if not."""
        assert isinstance(name, unicode)
        d = self._read()
        d.addCallback(lambda children: children.has_key(name))
        return d

    def _get(self, children, name):
        child = children.get(name)
        if child is None:
            raise NoSuchChildError(name)
        return child[0]

    def _get_with_metadata(self, children, name):
        child = children.get(name)
        if child is None:
            raise NoSuchChildError(name)
        return child

    def get(self, name):
        """I return a Deferred that fires with the named child node,
        which is either an IFileNode or an IDirectoryNode."""
        assert isinstance(name, unicode)
        d = self._read()
        d.addCallback(self._get, name)
        return d

    def get_child_and_metadata(self, name):
        """I return a Deferred that fires with the (node, metadata) pair for
        the named child. The node is either an IFileNode or an
        IDirectoryNode, and the metadata is a dictionary."""
        assert isinstance(name, unicode)
        d = self._read()
        d.addCallback(self._get_with_metadata, name)
        return d

    def get_metadata_for(self, name):
        assert isinstance(name, unicode)
        d = self._read()
        d.addCallback(lambda children: children[name][1])
        return d

    def set_metadata_for(self, name, metadata):
        assert isinstance(name, unicode)
        if self.is_readonly():
            return defer.fail(NotMutableError())
        assert isinstance(metadata, dict)
        s = MetadataSetter(self, name, metadata)
        d = self._node.modify(s.modify)
        d.addCallback(lambda res: self)
        return d

    def get_child_at_path(self, path):
        """Transform a child path into an IDirectoryNode or IFileNode.

        I perform a recursive series of 'get' operations to find the named
        descendant node. I return a Deferred that fires with the node, or
        errbacks with IndexError if the node could not be found.

        The path can be either a single string (slash-separated) or a list of
        path-name elements.
        """
        d = self.get_child_and_metadata_at_path(path)
        d.addCallback(lambda (node, metadata): node)
        return d

    def get_child_and_metadata_at_path(self, path):
        """Transform a child path into an IDirectoryNode or IFileNode and
        a metadata dictionary from the last edge that was traversed.
        """

        if not path:
            return defer.succeed((self, {}))
        if isinstance(path, (list, tuple)):
            pass
        else:
            path = path.split("/")
        for p in path:
            assert isinstance(p, unicode)
        childname = path[0]
        remaining_path = path[1:]
        if remaining_path:
            d = self.get(childname)
            d.addCallback(lambda node:
                          node.get_child_and_metadata_at_path(remaining_path))
            return d
        d = self.get_child_and_metadata(childname)
        return d

    def set_uri(self, name, child_uri, metadata=None, overwrite=True):
        """I add a child (by URI) at the specific name. I return a Deferred
        that fires with the child node when the operation finishes. I will
        replace any existing child of the same name.

        The child_uri could be for a file, or for a directory (either
        read-write or read-only, using a URI that came from get_uri() ).

        If this directory node is read-only, the Deferred will errback with a
        NotMutableError."""
        assert isinstance(name, unicode)
        child_node = self._create_node(child_uri)
        d = self.set_node(name, child_node, metadata, overwrite)
        d.addCallback(lambda res: child_node)
        return d

    def set_children(self, entries, overwrite=True):
        # this takes URIs
        a = Adder(self, overwrite=overwrite)
        node_entries = []
        for e in entries:
            if len(e) == 2:
                name, child_uri = e
                metadata = None
            else:
                assert len(e) == 3
                name, child_uri, metadata = e
            assert isinstance(name, unicode)
            a.set_node(name, self._create_node(child_uri), metadata)
        return self._node.modify(a.modify)

    def set_node(self, name, child, metadata=None, overwrite=True):
        """I add a child at the specific name. I return a Deferred that fires
        when the operation finishes. This Deferred will fire with the child
        node that was just added. I will replace any existing child of the
        same name.

        If this directory node is read-only, the Deferred will errback with a
        NotMutableError."""

        if self.is_readonly():
            return defer.fail(NotMutableError())
        assert isinstance(name, unicode)
        assert IFilesystemNode.providedBy(child), child
        a = Adder(self, overwrite=overwrite)
        a.set_node(name, child, metadata)
        d = self._node.modify(a.modify)
        d.addCallback(lambda res: child)
        return d

    def set_nodes(self, entries, overwrite=True):
        if self.is_readonly():
            return defer.fail(NotMutableError())
        a = Adder(self, entries, overwrite=overwrite)
        d = self._node.modify(a.modify)
        d.addCallback(lambda res: None)
        return d


    def add_file(self, name, uploadable, metadata=None, overwrite=True):
        """I upload a file (using the given IUploadable), then attach the
        resulting FileNode to the directory at the given name. I return a
        Deferred that fires (with the IFileNode of the uploaded file) when
        the operation completes."""
        assert isinstance(name, unicode)
        if self.is_readonly():
            return defer.fail(NotMutableError())
        d = self._client.upload(uploadable)
        d.addCallback(lambda results: results.uri)
        d.addCallback(self._client.create_node_from_uri)
        d.addCallback(lambda node:
                      self.set_node(name, node, metadata, overwrite))
        return d

    def delete(self, name):
        """I remove the child at the specific name. I return a Deferred that
        fires (with the node just removed) when the operation finishes."""
        assert isinstance(name, unicode)
        if self.is_readonly():
            return defer.fail(NotMutableError())
        deleter = Deleter(self, name)
        d = self._node.modify(deleter.modify)
        d.addCallback(lambda res: deleter.old_child)
        return d

    def create_empty_directory(self, name, overwrite=True):
        """I create and attach an empty directory at the given name. I return
        a Deferred that fires (with the new directory node) when the
        operation finishes."""
        assert isinstance(name, unicode)
        if self.is_readonly():
            return defer.fail(NotMutableError())
        d = self._client.create_empty_dirnode()
        def _created(child):
            entries = [(name, child, None)]
            a = Adder(self, entries, overwrite=overwrite)
            d = self._node.modify(a.modify)
            d.addCallback(lambda res: child)
            return d
        d.addCallback(_created)
        return d

    def move_child_to(self, current_child_name, new_parent,
                      new_child_name=None, overwrite=True):
        """I take one of my children and move them to a new parent. The child
        is referenced by name. On the new parent, the child will live under
        'new_child_name', which defaults to 'current_child_name'. I return a
        Deferred that fires when the operation finishes."""
        assert isinstance(current_child_name, unicode)
        if self.is_readonly() or new_parent.is_readonly():
            return defer.fail(NotMutableError())
        if new_child_name is None:
            new_child_name = current_child_name
        assert isinstance(new_child_name, unicode)
        d = self.get(current_child_name)
        def sn(child):
            return new_parent.set_node(new_child_name, child,
                                       overwrite=overwrite)
        d.addCallback(sn)
        d.addCallback(lambda child: self.delete(current_child_name))
        return d


    def deep_traverse(self, walker):
        """Perform a recursive walk, using this dirnode as a root, notifying
        the 'walker' instance of everything I encounter.

        I call walker.enter_directory(parent, children) once for each dirnode
        I visit, immediately after retrieving the list of children. I pass in
        the parent dirnode and the dict of childname->(childnode,metadata).
        This function should *not* traverse the children: I will do that.
        enter_directory() is most useful for the deep-stats number that
        counts how large a directory is.

        I call walker.add_node(node, path) for each node (both files and
        directories) I can reach. Most work should be done here.

        I avoid loops by keeping track of verifier-caps and refusing to call
        each() or traverse a node that I've seen before.

        I return a Deferred that will fire with the value of walker.finish().
        """

        # this is just a tree-walker, except that following each edge
        # requires a Deferred. We use a ConcurrencyLimiter to make sure the
        # fan-out doesn't cause problems.

        monitor = Monitor()
        walker.set_monitor(monitor)

        found = set([self.get_verifier()])
        limiter = ConcurrencyLimiter(10)
        d = self._deep_traverse_dirnode(self, [],
                                        walker, monitor, found, limiter)
        d.addCallback(lambda ignored: walker.finish())
        d.addBoth(monitor.finish)
        d.addErrback(lambda f: None)

        return monitor

    def _deep_traverse_dirnode(self, node, path,
                               walker, monitor, found, limiter):
        # process this directory, then walk its children
        monitor.raise_if_cancelled()
        d = limiter.add(walker.add_node, node, path)
        d.addCallback(lambda ignored: limiter.add(node.list))
        d.addCallback(self._deep_traverse_dirnode_children, node, path,
                      walker, monitor, found, limiter)
        return d

    def _deep_traverse_dirnode_children(self, children, parent, path,
                                        walker, monitor, found, limiter):
        monitor.raise_if_cancelled()
        dl = [limiter.add(walker.enter_directory, parent, children)]
        for name, (child, metadata) in children.iteritems():
            verifier = child.get_verifier()
            # allow LIT files (for which verifier==None) to be processed
            if (verifier is not None) and (verifier in found):
                continue
            found.add(verifier)
            childpath = path + [name]
            if IDirectoryNode.providedBy(child):
                dl.append(self._deep_traverse_dirnode(child, childpath,
                                                      walker, monitor,
                                                      found, limiter))
            else:
                dl.append(limiter.add(walker.add_node, child, childpath))
        return defer.DeferredList(dl, fireOnOneErrback=True, consumeErrors=True)


    def build_manifest(self):
        """Return a Monitor, with a ['status'] that will be a list of (path,
        cap) tuples, for all nodes (directories and files) reachable from
        this one."""
        walker = ManifestWalker(self)
        return self.deep_traverse(walker)

    def start_deep_stats(self):
        # Since deep_traverse tracks verifier caps, we avoid double-counting
        # children for which we've got both a write-cap and a read-cap
        return self.deep_traverse(DeepStats(self))

    def start_deep_check(self, verify=False):
        return self.deep_traverse(DeepChecker(self, verify, repair=False))

    def start_deep_check_and_repair(self, verify=False):
        return self.deep_traverse(DeepChecker(self, verify, repair=True))



class DeepStats:
    def __init__(self, origin):
        self.origin = origin
        self.stats = {}
        for k in ["count-immutable-files",
                  "count-mutable-files",
                  "count-literal-files",
                  "count-files",
                  "count-directories",
                  "size-immutable-files",
                  #"size-mutable-files",
                  "size-literal-files",
                  "size-directories",
                  "largest-directory",
                  "largest-directory-children",
                  "largest-immutable-file",
                  #"largest-mutable-file",
                  ]:
            self.stats[k] = 0
        self.histograms = {}
        for k in ["size-files-histogram"]:
            self.histograms[k] = {} # maps (min,max) to count
        self.buckets = [ (0,0), (1,3)]
        self.root = math.sqrt(10)

    def set_monitor(self, monitor):
        self.monitor = monitor
        monitor.origin_si = self.origin.get_storage_index()
        monitor.set_status(self.stats)

    def add_node(self, node, childpath):
        if IDirectoryNode.providedBy(node):
            self.add("count-directories")
        elif IMutableFileNode.providedBy(node):
            self.add("count-files")
            self.add("count-mutable-files")
            # TODO: update the servermap, compute a size, add it to
            # size-mutable-files, max it into "largest-mutable-file"
        elif IFileNode.providedBy(node): # CHK and LIT
            self.add("count-files")
            size = node.get_size()
            self.histogram("size-files-histogram", size)
            if node.get_uri().startswith("URI:LIT:"):
                self.add("count-literal-files")
                self.add("size-literal-files", size)
            else:
                self.add("count-immutable-files")
                self.add("size-immutable-files", size)
                self.max("largest-immutable-file", size)

    def enter_directory(self, parent, children):
        dirsize_bytes = parent.get_size()
        dirsize_children = len(children)
        self.add("size-directories", dirsize_bytes)
        self.max("largest-directory", dirsize_bytes)
        self.max("largest-directory-children", dirsize_children)

    def add(self, key, value=1):
        self.stats[key] += value

    def max(self, key, value):
        self.stats[key] = max(self.stats[key], value)

    def which_bucket(self, size):
        # return (min,max) such that min <= size <= max
        # values are from the set (0,0), (1,3), (4,10), (11,31), (32,100),
        # (101,316), (317, 1000), etc: two per decade
        assert size >= 0
        i = 0
        while True:
            if i >= len(self.buckets):
                # extend the list
                new_lower = self.buckets[i-1][1]+1
                new_upper = int(mathutil.next_power_of_k(new_lower, self.root))
                self.buckets.append( (new_lower, new_upper) )
            maybe = self.buckets[i]
            if maybe[0] <= size <= maybe[1]:
                return maybe
            i += 1

    def histogram(self, key, size):
        bucket = self.which_bucket(size)
        h = self.histograms[key]
        if bucket not in h:
            h[bucket] = 0
        h[bucket] += 1

    def get_results(self):
        stats = self.stats.copy()
        for key in self.histograms:
            h = self.histograms[key]
            out = [ (bucket[0], bucket[1], h[bucket]) for bucket in h ]
            out.sort()
            stats[key] = out
        return stats

    def finish(self):
        return self.get_results()

class ManifestWalker(DeepStats):
    def __init__(self, origin):
        DeepStats.__init__(self, origin)
        self.manifest = []

    def add_node(self, node, path):
        self.manifest.append( (tuple(path), node.get_uri()) )
        return DeepStats.add_node(self, node, path)

    def finish(self):
        return {"manifest": self.manifest,
                "stats": self.get_results(),
                }


class DeepChecker:
    def __init__(self, root, verify, repair):
        root_si = root.get_storage_index()
        self._lp = log.msg(format="deep-check starting (%(si)s),"
                           " verify=%(verify)s, repair=%(repair)s",
                           si=base32.b2a(root_si), verify=verify, repair=repair)
        self._verify = verify
        self._repair = repair
        if repair:
            self._results = DeepCheckAndRepairResults(root_si)
        else:
            self._results = DeepCheckResults(root_si)
        self._stats = DeepStats(root)

    def set_monitor(self, monitor):
        self.monitor = monitor
        monitor.set_status(self._results)

    def add_node(self, node, childpath):
        if self._repair:
            d = node.check_and_repair(self.monitor, self._verify)
            d.addCallback(self._results.add_check_and_repair, childpath)
        else:
            d = node.check(self.monitor, self._verify)
            d.addCallback(self._results.add_check, childpath)
        d.addCallback(lambda ignored: self._stats.add_node(node, childpath))
        return d

    def enter_directory(self, parent, children):
        return self._stats.enter_directory(parent, children)

    def finish(self):
        log.msg("deep-check done", parent=self._lp)
        self._results.update_stats(self._stats.get_results())
        return self._results


# use client.create_dirnode() to make one of these


