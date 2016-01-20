#!/usr/bin/python
import os

from twisted.trial import unittest
from allmydata.util.fileutil import write, remove
from allmydata.client import Client
from allmydata.scripts.create_node import write_node_config
from allmydata.web.root import Root

INTRODUCERS_CFG_FURLS=['furl1', 'furl2']
INTRODUCERS_CFG_FURLS_COMMENTED=['furl1', '#furl2', 'furl3']

class MultiIntroTests(unittest.TestCase):

    def setUp(self):
        # setup tahoe.cfg and basedir/private/introducers
        # create a custom tahoe.cfg
        self.basedir = os.path.dirname(self.mktemp())
        c = open(os.path.join(self.basedir, "tahoe.cfg"), "w")
        config = {}
        write_node_config(c, config)
        fake_furl = "furl1"
        c.write("[client]\n")
        c.write("introducer.furl = %s\n" % fake_furl)
        c.close()
        os.mkdir(os.path.join(self.basedir,"private"))
        self.introducers_file = os.path.join(self.basedir,"private", "introducers")

        # create a basedir/private/introducers
        write(self.introducers_file, '\n'.join(INTRODUCERS_CFG_FURLS))

    def test_introducer_count(self):
        """ Ensure that the Client creates same number of introducer clients
        as found in "basedir/private/introducers" config file. """
        write(self.introducers_file, '\n'.join(INTRODUCERS_CFG_FURLS))

        # get a client and count of introducer_clients
        myclient = Client(self.basedir)
        ic_count = len(myclient.introducer_clients)

        # assertions
        self.failUnlessEqual(ic_count, 2)

    def test_introducer_count_commented(self):
        """ Ensure that the Client creates same number of introducer clients
        as found in "basedir/private/introducers" config file when there is one
        commented."""
        write(self.introducers_file, '\n'.join(INTRODUCERS_CFG_FURLS_COMMENTED))
        # get a client and count of introducer_clients
        myclient = Client(self.basedir)
        ic_count = len(myclient.introducer_clients)

        # assertions
        self.failUnlessEqual(ic_count, 2)

    def test_read_introducer_furl_from_tahoecfg(self):
        """ Ensure that the Client reads the introducer.furl config item from
        the tahoe.cfg file. """
        # create a custom tahoe.cfg
        c = open(os.path.join(self.basedir, "tahoe.cfg"), "w")
        config = {}
        write_node_config(c, config)
        fake_furl = "furl1"
        c.write("[client]\n")
        c.write("introducer.furl = %s\n" % fake_furl)
        c.close()

        # get a client and first introducer_furl
        myclient = Client(self.basedir)
        tahoe_cfg_furl = myclient.introducer_furls[0]

        # assertions
        self.failUnlessEqual(fake_furl, tahoe_cfg_furl)

    def test_warning(self):
        """ Ensure that the Client warns user if the the introducer.furl config
        item from the tahoe.cfg file is copied to "introducers" cfg file. """
        # prepare tahoe.cfg
        c = open(os.path.join(self.basedir,"tahoe.cfg"), "w")
        config = {}
        write_node_config(c, config)
        fake_furl = "furl0"
        c.write("[client]\n")
        c.write("introducer.furl = %s\n" % fake_furl)
        c.close()

        # prepare "basedir/private/introducers"
        write(self.introducers_file, '\n'.join(INTRODUCERS_CFG_FURLS))

        # get a client
        myclient = Client(self.basedir)

        # assertions: we expect a warning as tahoe_cfg furl is different
        self.failUnlessEqual(True, myclient.warn_flag)


if __name__ == "__main__":
    unittest.main()
