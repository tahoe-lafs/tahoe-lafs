from twisted.trial import unittest
from twisted.internet import defer
from allmydata.interfaces import UploadUnhappinessError
from allmydata.test.no_network import GridTestMixin
from allmydata.interfaces import MDMF_VERSION

from allmydata.mutable.filenode import MutableFileNode
from allmydata.mutable.publish import MutableData

import allmydata.test.common_util as testutil

class ServersOfHappiness(GridTestMixin, unittest.TestCase, testutil.ShouldFailMixin):

    def setUp(self):
        self.data = "testdata " * 100000 # about 900 KiB; MDMF
        self.small_data = "test data" * 10 # about 90 B; SDMF

    def do_upload_mdmf(self):
        d = self.nm.create_mutable_file(MutableData(self.data),
                                        version=MDMF_VERSION)
        def _then(n):
            assert isinstance(n, MutableFileNode)
            self.mdmf_node = n
            return n
        d.addCallback(_then)
        return d

    def do_upload_sdmf(self):
        d = self.nm.create_mutable_file(MutableData(self.small_data))
        def _then(n):
            assert isinstance(n, MutableFileNode)
            self.sdmf_node = n
            return n
        d.addCallback(_then)
        return d

    def test_basic_success_sdmf(self):
        GridTestMixin.setUp(self)
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c = self.g.clients[0]
        self.nm = self.c.nodemaker
        d = self.do_upload_sdmf()
        return d

    def test_basic_success_mdmf(self):
        GridTestMixin.setUp(self)
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c = self.g.clients[0]
        self.nm = self.c.nodemaker
        d = self.do_upload_mdmf()
        return d

    def test_basic_failure_sdmf(self):
        GridTestMixin.setUp(self)
        self.basedir = self.mktemp()
        self.set_up_grid(num_servers=6)
        self.c = self.g.clients[0]
        self.nm = self.c.nodemaker
        d = self.shouldFail(UploadUnhappinessError, "test_failure_sdmf",
                            "shares could be placed on only 6 server(s) such that any 3 "
                            "of them have enough shares to recover the file, but we were asked to "
                            "place shares on at least 7 servers.",
                            self.nm.create_mutable_file,
                            MutableData(self.small_data))
        return d

    def test_basic_failure_mdmf(self):
        GridTestMixin.setUp(self)
        self.basedir = self.mktemp()
        self.set_up_grid(num_servers=6)
        self.c = self.g.clients[0]
        self.nm = self.c.nodemaker
        d = self.shouldFail(UploadUnhappinessError, "test_failure_mdmf",
                            "shares could be placed on only 6 server(s) such that any 3 "
                            "of them have enough shares to recover the file, but we were asked to "
                            "place shares on at least 7 servers.",
                            self.nm.create_mutable_file,
                            MutableData(self.data), version=MDMF_VERSION)
        return d

    def test_update_failure_sdmf(self):
        # Upload a file to the grid and remove a server such that
        # the number of servers is less than the servers of happiness requirement.
        # Any attempts to update the file should fail.

        GridTestMixin.setUp(self)
        self.basedir = self.mktemp()
        self.set_up_grid(num_servers=7)
        self.c = self.g.clients[0]
        self.nm = self.c.nodemaker

        d = self.do_upload_sdmf()

        def _setup(ign):
            d = defer.succeed(None)
            d.addCallback(lambda ign:
                            self.g.remove_server(self.g.servers_by_number[0].my_nodeid))
            d.addCallback(lambda ign, node=self.sdmf_node:
                            node.get_best_mutable_version())
            return d

        def _check(mv):
            d = self.shouldFail(UploadUnhappinessError, "test_update_failure_sdmf",
                            "shares could be placed on only 6 server(s) such that any 3 "
                            "of them have enough shares to recover the file, but we were asked to "
                            "place shares on at least 7 servers.",
                            mv.update, MutableData("appended"), len(self.small_data))
            return d

        d.addCallback(_setup)
        d.addCallback(_check)
        return d

    def test_update_failure_mdmf(self):
        # Upload a file to the grid and remove a server such that
        # the number of servers is less than the servers of happiness requirement.
        # Any attempts to update the file should fail.

        GridTestMixin.setUp(self)
        self.basedir = self.mktemp()
        self.set_up_grid(num_servers=7)
        self.c = self.g.clients[0]
        self.nm = self.c.nodemaker

        d = self.do_upload_mdmf()

        def _setup(ign):
            d = defer.succeed(None)
            d.addCallback(lambda ign:
                            self.g.remove_server(self.g.servers_by_number[0].my_nodeid))
            d.addCallback(lambda ign, node=self.mdmf_node:
                            node.get_best_mutable_version())
            return d

        def _check(mv):
            d = self.shouldFail(UploadUnhappinessError, "test_update_failure_mdmf",
                            "shares could be placed on only 6 server(s) such that any 3 "
                            "of them have enough shares to recover the file, but we were asked to "
                            "place shares on at least 7 servers.",
                            mv.update, MutableData("appended"), len(self.data))
            return d

        d.addCallback(_setup)
        d.addCallback(_check)
        return d
