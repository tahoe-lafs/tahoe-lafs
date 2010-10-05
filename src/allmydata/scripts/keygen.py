
import os, sys
from allmydata.scripts.common import BasedirMixin, BaseOptions
from allmydata.util.assertutil import precondition
from allmydata.util.encodingutil import listdir_unicode, quote_output

class CreateKeyGeneratorOptions(BasedirMixin, BaseOptions):
    default_nodedir = None

    optParameters = [
        ["node-directory", "d", None, "Specify which directory the key-generator should be created in. [no default]"],
    ]

keygen_tac = """
# -*- python -*-

import pkg_resources
pkg_resources.require('allmydata-tahoe')

from allmydata import key_generator
from twisted.application import service

k = key_generator.KeyGeneratorService(default_key_size=2048)
#k.key_generator.verbose = False
#k.key_generator.pool_size = 16
#k.key_generator.pool_refresh_delay = 6

application = service.Application("allmydata_key_generator")
k.setServiceParent(application)
"""

def create_key_generator(basedir, config, out=sys.stdout, err=sys.stderr):
    # This should always be called with an absolute Unicode basedir.
    precondition(isinstance(basedir, unicode), basedir)

    if os.path.exists(basedir):
        if listdir_unicode(basedir):
            print >>err, "The base directory %s is not empty." % quote_output(basedir)
            print >>err, "To avoid clobbering anything, I am going to quit now."
            print >>err, "Please use a different directory, or empty this one."
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    f = open(os.path.join(basedir, "tahoe-key-generator.tac"), "wb")
    f.write(keygen_tac)
    f.close()
    return 0

subCommands = [
    ["create-key-generator", None, CreateKeyGeneratorOptions, "Create a key generator service."],
]

dispatch = {
    "create-key-generator": create_key_generator,
    }

