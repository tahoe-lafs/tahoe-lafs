
from twisted.trial import unittest

from allmydata.frontends import ftpd
from allmydata.immutable import upload
from allmydata.mutable import publish
from allmydata.test.no_network import GridTestMixin
from allmydata.test.common_util import ReallyEqualMixin

class Handler(GridTestMixin, ReallyEqualMixin, unittest.TestCase):
    """
    This is a no-network unit test of ftpd.Handler and the abstractions
    it uses.
    """

    FALL_OF_BERLIN_WALL = 626644800
    TURN_OF_MILLENIUM = 946684800

    def _set_up(self, basedir, num_clients=1, num_servers=10):
        self.basedir = "ftp/" + basedir
        self.set_up_grid(num_clients=num_clients, num_servers=num_servers)

        self.client = self.g.clients[0]
        self.username = "alice"
        self.convergence = ""

        d = self.client.create_dirnode()
        def _created_root(node):
            self.root = node
            self.root_uri = node.get_uri()
            self.handler = ftpd.Handler(self.client, self.root, self.username,
                                        self.convergence)
        d.addCallback(_created_root)
        return d

    def _set_metadata(self, name, metadata):
        """Set metadata for `name', avoiding MetadataSetter's timestamp reset
        behavior."""
        def _modifier(old_contents, servermap, first_time):
            children = self.root._unpack_contents(old_contents)
            children[name] = (children[name][0], metadata)
            return self.root._pack_contents(children)

        return self.root._node.modify(_modifier)

    def _set_up_tree(self):
        # add immutable file at root
        immutable = upload.Data("immutable file contents", None)
        d = self.root.add_file(u"immutable", immutable)

        # `mtime' and `linkmotime' both set
        md_both = {'mtime': self.FALL_OF_BERLIN_WALL,
                   'tahoe': {'linkmotime': self.TURN_OF_MILLENIUM}}
        d.addCallback(lambda _: self._set_metadata(u"immutable", md_both))

        # add link to root from root
        d.addCallback(lambda _: self.root.set_node(u"loop", self.root))

        # `mtime' set, but no `linkmotime'
        md_just_mtime = {'mtime': self.FALL_OF_BERLIN_WALL, 'tahoe': {}}
        d.addCallback(lambda _: self._set_metadata(u"loop", md_just_mtime))

        # add mutable file at root
        mutable = publish.MutableData("mutable file contents")
        d.addCallback(lambda _: self.client.create_mutable_file(mutable))
        d.addCallback(lambda node: self.root.set_node(u"mutable", node))

        # neither `mtime' nor `linkmotime' set
        d.addCallback(lambda _: self._set_metadata(u"mutable", {}))

        return d

    def _compareDirLists(self, actual, expected):
        actual_list = sorted(actual)
        expected_list = sorted(expected)

        self.failUnlessReallyEqual(len(actual_list), len(expected_list),
                                   "%r is wrong length, expecting %r" % (
                                       actual_list, expected_list))
        for (a, b) in zip(actual_list, expected_list):
           (name, meta) = a
           # convert meta.permissions to int for comparison. When we run
           # against many (but not all) versions of Twisted, this is a
           # filepath.Permissions object, not an int
           meta = list(meta)
           meta[2] = meta[2] & 0xffffffff
           (expected_name, expected_meta) = b
           self.failUnlessReallyEqual(name, expected_name)
           self.failUnlessReallyEqual(meta, expected_meta)

    def test_list(self):
        keys = ("size", "directory", "permissions", "hardlinks", "modified",
                "owner", "group", "unexpected")
        d = self._set_up("list")

        d.addCallback(lambda _: self._set_up_tree())
        d.addCallback(lambda _: self.handler.list("", keys=keys))

        expected_root = [
            ('loop',
             [0, True, 0600, 1, self.FALL_OF_BERLIN_WALL, 'alice', 'alice', '??']),
            ('immutable',
             [23, False, 0600, 1, self.TURN_OF_MILLENIUM, 'alice', 'alice', '??']),
            ('mutable',
             # timestamp should be 0 if no timestamp metadata is present
             [0, False, 0600, 1, 0, 'alice', 'alice', '??'])]

        d.addCallback(lambda root: self._compareDirLists(root, expected_root))

        return d
