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
        config = {'hide-ip':False, 'listen': 'tcp',
                  'port': None, 'location': None, 'hostname': 'example.net'}
        write_node_config(c, config)
        fake_furl = "furl1"
        c.write("[client]\n")
        c.write("introducer.furl = %s\n" % fake_furl)
        c.write("[storage]\n")
        c.write("enabled = false\n")
        c.close()
        os.mkdir(os.path.join(self.basedir,"private"))
        self.yaml_path = FilePath(os.path.join(self.basedir, "private",
                                               "introducers.yaml"))

    def test_introducer_count(self):
        """ Ensure that the Client creates same number of introducer clients
        as found in "basedir/private/introducers" config file. """
        connections = {'introducers':
            {
            u'intro1':{ 'furl': 'furl1' },
            u'intro2':{ 'furl': 'furl4' }
        },
        }
        self.yaml_path.setContent(yamlutil.safe_dump(connections))
        # get a client and count of introducer_clients
        myclient = Client(self.basedir)
        ic_count = len(myclient.introducer_clients)

        # assertions
        self.failUnlessEqual(ic_count, 3)

    def test_introducer_count_commented(self):
        """ Ensure that the Client creates same number of introducer clients
        as found in "basedir/private/introducers" config file when there is one
        commented."""
        self.yaml_path.setContent(INTRODUCERS_CFG_FURLS_COMMENTED)
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
        config = {'hide-ip':False, 'listen': 'tcp',
                  'port': None, 'location': None, 'hostname': 'example.net'}
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

    def test_reject_default_in_yaml(self):
        connections = {'introducers': {
            u'default': { 'furl': 'furl1' },
            }}
        self.yaml_path.setContent(yamlutil.safe_dump(connections))
        e = self.assertRaises(ValueError, Client, self.basedir)
        self.assertEquals(str(e), "'default' introducer furl cannot be specified in introducers.yaml; please fix impossible configuration.")

SIMPLE_YAML = """
introducers:
  one:
    furl: furl1
"""

# this format was recommended in docs/configuration.rst in 1.12.0, but it
# isn't correct (the "furl = furl1" line is recorded as the string value of
# the ["one"] key, instead of being parsed as a single-key dictionary).
EQUALS_YAML = """
introducers:
  one: furl = furl1
"""

class NoDefault(unittest.TestCase):
    def setUp(self):
        # setup tahoe.cfg and basedir/private/introducers
        # create a custom tahoe.cfg
        self.basedir = os.path.dirname(self.mktemp())
        c = open(os.path.join(self.basedir, "tahoe.cfg"), "w")
        config = {'hide-ip':False, 'listen': 'tcp',
                  'port': None, 'location': None, 'hostname': 'example.net'}
        write_node_config(c, config)
        c.write("[client]\n")
        c.write("# introducer.furl =\n") # omit default
        c.write("[storage]\n")
        c.write("enabled = false\n")
        c.close()
        os.mkdir(os.path.join(self.basedir,"private"))
        self.yaml_path = FilePath(os.path.join(self.basedir, "private",
                                               "introducers.yaml"))

    def test_ok(self):
        connections = {'introducers': {
            u'one': { 'furl': 'furl1' },
            }}
        self.yaml_path.setContent(yamlutil.safe_dump(connections))
        myclient = Client(self.basedir)
        tahoe_cfg_furl = myclient.introducer_furls[0]
        self.assertEquals(tahoe_cfg_furl, 'furl1')

    def test_real_yaml(self):
        self.yaml_path.setContent(SIMPLE_YAML)
        myclient = Client(self.basedir)
        tahoe_cfg_furl = myclient.introducer_furls[0]
        self.assertEquals(tahoe_cfg_furl, 'furl1')

    def test_invalid_equals_yaml(self):
        self.yaml_path.setContent(EQUALS_YAML)
        e = self.assertRaises(TypeError, Client, self.basedir)
        self.assertEquals(str(e), "string indices must be integers")

    def test_introducerless(self):
        connections = {'introducers': {} }
        self.yaml_path.setContent(yamlutil.safe_dump(connections))
        myclient = Client(self.basedir)
        self.assertEquals(len(myclient.introducer_furls), 0)

if __name__ == "__main__":
    unittest.main()
