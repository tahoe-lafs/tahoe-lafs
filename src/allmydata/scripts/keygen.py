
import os, sys

from allmydata.scripts.common import NoDefaultBasedirOptions
from allmydata.scripts.create_node import write_tac
from allmydata.util.assertutil import precondition
from allmydata.util.encodingutil import listdir_unicode, quote_output


class CreateKeyGeneratorOptions(NoDefaultBasedirOptions):
    subcommand_name = "create-key-generator"


def create_key_generator(config, out=sys.stdout, err=sys.stderr):
    basedir = config['basedir']
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
    write_tac(basedir, "key-generator")
    return 0

subCommands = [
    ["create-key-generator", None, CreateKeyGeneratorOptions, "Create a key generator service."],
]

dispatch = {
    "create-key-generator": create_key_generator,
    }

