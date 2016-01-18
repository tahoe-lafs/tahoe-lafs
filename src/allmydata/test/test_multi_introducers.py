#!/usr/bin/python
import unittest, os
from mock import Mock, patch

from allmydata.util.fileutil import write, remove
from allmydata.client import Client, MULTI_INTRODUCERS_CFG
from allmydata.scripts.create_node import write_node_config
from allmydata.web.root import Root

INTRODUCERS_CFG_FURLS=['furl1', 'furl2']

def cfg_setup():
    # setup tahoe.cfg and basedir/introducers
    # create a custom tahoe.cfg
    c = open(os.path.join("tahoe.cfg"), "w")
    config = {}
    write_node_config(c, config)
    fake_furl = "furl1"
    c.write("[client]\n")
    c.write("introducer.furl = %s\n" % fake_furl)
    c.close()

    # create a basedir/introducers
    write(MULTI_INTRODUCERS_CFG, '\n'.join(INTRODUCERS_CFG_FURLS))

def cfg_cleanup():
    # clean-up all cfg files
    remove("tahoe.cfg")
    remove(MULTI_INTRODUCERS_CFG)

class TestRoot(unittest.TestCase):

    def setUp(self):
        cfg_setup()

    def tearDown(self):
        cfg_cleanup()

    @patch('allmydata.web.root.Root')
    def test_introducer_furls(self, MockRoot):
        """Ensure that a client's 'welcome page can fetch all introducer FURLs
         loaded by the Client"""

        # mock setup
        mockctx = Mock()
        mockdata = Mock()

        # get the Client and furl count
        myclient = Client()
        furls = myclient.introducer_furls
        furl_count = len(furls)

        # Pass mock value to Root
        myroot = Root(myclient)

        # make the call
        s = myroot.data_introducers(mockctx, mockdata)

        #assertions: compare return value with preset value
        self.failUnlessEqual(furl_count, len(s))



class TestClient(unittest.TestCase):
    def setUp(self):
        cfg_setup()

    def tearDown(self):
        cfg_cleanup()

    def test_introducer_count(self):
        """ Ensure that the Client creates same number of introducer clients
        as found in "basedir/introducers" config file. """
        write(MULTI_INTRODUCERS_CFG, '\n'.join(INTRODUCERS_CFG_FURLS))

        # get a client and count of introducer_clients
        myclient = Client()
        ic_count = len(myclient.introducer_clients)

        # assertions
        self.failUnlessEqual(ic_count, 2)

    def test_read_introducer_furl_from_tahoecfg(self):
        """ Ensure that the Client reads the introducer.furl config item from
        the tahoe.cfg file. """
        # create a custom tahoe.cfg
        c = open(os.path.join("tahoe.cfg"), "w")
        config = {}
        write_node_config(c, config)
        fake_furl = "furl1"
        c.write("[client]\n")
        c.write("introducer.furl = %s\n" % fake_furl)
        c.close()

        # get a client and first introducer_furl
        myclient = Client()
        tahoe_cfg_furl = myclient.introducer_furls[0]

        # assertions
        self.failUnlessEqual(fake_furl, tahoe_cfg_furl)

    def test_warning(self):
        """ Ensure that the Client warns user if the the introducer.furl config item from the tahoe.cfg file is copied to "introducers" cfg file """
        # prepare tahoe.cfg
        c = open(os.path.join("tahoe.cfg"), "w")
        config = {}
        write_node_config(c, config)
        fake_furl = "furl0"
        c.write("[client]\n")
        c.write("introducer.furl = %s\n" % fake_furl)
        c.close()

        # prepare "basedir/introducers"
        write(MULTI_INTRODUCERS_CFG, '\n'.join(INTRODUCERS_CFG_FURLS))

        # get a client
        myclient = Client()

        # assertions: we expect a warning as tahoe_cfg furl is different
        self.failUnlessEqual(True, myclient.warn_flag)


if __name__ == "__main__":
    unittest.main()
