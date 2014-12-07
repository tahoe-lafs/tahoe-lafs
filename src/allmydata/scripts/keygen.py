
import sys
from allmydata.scripts.common import NoDefaultBasedirOptions, create_basedir, NonEmptyBasedirException
from allmydata.util.encodingutil import quote_output


class CreateKeyGeneratorOptions(NoDefaultBasedirOptions):
    subcommand_name = "create-key-generator"


def create_key_generator(config, out=sys.stdout, err=sys.stderr):
    basedir = config['basedir']
    try:
        create_basedir(basedir, "key-generator", err=err)
    except NonEmptyBasedirException:
        return -1

    print >>out, "Key generator created in %s" % quote_output(basedir)
    return 0

subCommands = [
    ["create-key-generator", None, CreateKeyGeneratorOptions, "Create a key generator service."],
]

dispatch = {
    "create-key-generator": create_key_generator,
    }

