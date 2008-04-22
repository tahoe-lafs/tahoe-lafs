
import os, sys
from twisted.python import usage
#from allmydata.scripts.common import BasedirMixin, NoDefaultBasedirMixin

class CreateKeyGeneratorOptions(usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to create the client in"],
        ]

keygen_tac = """
# -*- python -*-

from allmydata import key_generator
from twisted.application import service

k = key_generator.KeyGeneratorService(2048)
#k.key_generator.verbose = False
#k.key_generator.pool_size = 16
#k.key_generator.pool_refresh_delay = 6

application = service.Application("allmydata_key_generator")
k.setServiceParent(application)
"""

def create_key_generator(config, out=sys.stdout, err=sys.stderr):
    basedir = config['basedir']
    if not basedir:
        print >>err, "a basedir was not provided, please use --basedir or -C"
        return -1
    if os.path.exists(basedir):
        if os.listdir(basedir):
            print >>err, "The base directory \"%s\", which is \"%s\" is not empty." % (basedir, os.path.abspath(basedir))
            print >>err, "To avoid clobbering anything, I am going to quit now."
            print >>err, "Please use a different directory, or empty this one."
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    f = open(os.path.join(basedir, "tahoe-key-generator.tac"), "wb")
    f.write(keygen_tac)
    f.close()

subCommands = [
    ["create-key-generator", None, CreateKeyGeneratorOptions, "Create a key generator service."],
]

dispatch = {
    "create-key-generator": create_key_generator,
    }

