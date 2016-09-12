#!/usr/bin/python
import os

from twisted.python.filepath import FilePath
from twisted.trial import unittest
from allmydata.util import yamlutil
from allmydata.client import Client
from allmydata.scripts.create_node import write_node_config

INTRODUCERS_CFG_FURLS=['furl1', 'furl2']
INTRODUCERS_CFG_FURLS_COMMENTED="""introducers:
  'intro1': {furl: furl1}
# 'intro2': {furl: furl4}
        """

class MultiIntroTests(unittest.TestCase):

    def setUp(self):
        # setup tahoe.cfg and basedir/private/introducers
        # create a custom tahoe.cfg
        self.basedir = os.path.dirname(self.mktemp())
        c = open(os.path.join(self.basedir, "tahoe.cfg"), "w")
        config = {'hide-ip':False}
        write_node_config(c, config)
        fake_furl = "furl1"
        c.write("[client]\n")
        c.write("introducer.furl = %s\n" % fake_furl)
        c.write("[storage]\n")
        c.write("enabled = false\n")
        c.close()
        os.mkdir(os.path.join(self.basedir,"private"))

    def test_introducer_count(self):
        """ Ensure that the Client creates same number of introducer clients
        as found in "basedir/private/introducers" config file. """
        connections = {'introducers':
            {
            u'intro1':{ 'furl': 'furl1' },
            u'intro2':{ 'furl': 'furl4' }
        },
        }
        connections_filepath = FilePath(os.path.join(self.basedir, "private", "introducers.yaml"))
        connections_filepath.setContent(yamlutil.safe_dump(connections))
        # get a client and count of introducer_clients
        myclient = Client(self.basedir)
        ic_count = len(myclient.introducer_clients)

        # assertions
        self.failUnlessEqual(ic_count, 3)

    def test_introducer_count_commented(self):
        """ Ensure that the Client creates same number of introducer clients
        as found in "basedir/private/introducers" config file when there is one
        commented."""
        connections_filepath = FilePath(os.path.join(self.basedir, "private", "introducers.yaml"))
        connections_filepath.setContent(INTRODUCERS_CFG_FURLS_COMMENTED)
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
        config = {'hide-ip':False}
        write_node_config(c, config)
        fake_furl = "furl1"
        c.write("[client]\n")
        c.write("introducer.furl = %s\n" % fake_furl)
        c.write("[storage]\n")
        c.write("enabled = false\n")
        c.close()

        # get a client and first introducer_furl
        myclient = Client(self.basedir)
        tahoe_cfg_furl = myclient.introducer_furls[0]

        # assertions
        self.failUnlessEqual(fake_furl, tahoe_cfg_furl)

if __name__ == "__main__":
    unittest.main()
