
import sys
from cStringIO import StringIO
from twisted.python import usage

from allmydata.scripts.common import BaseOptions
import debug, create_node, startstop_node, cli, keygen

_general_commands = ( create_node.subCommands
                    + keygen.subCommands
                    + debug.subCommands
                    + cli.subCommands
                    )

class Options(BaseOptions, usage.Options):
    synopsis = "Usage:  tahoe <command> [command options]"

    subCommands = []
    subCommands += _general_commands
    subCommands += startstop_node.subCommands

    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a command")

def runner(argv,
           run_by_human=True,
           stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
           install_node_control=True, additional_commands=None):

    config = Options()
    if install_node_control:
        config.subCommands.extend(startstop_node.subCommands)

    ac_dispatch = {}
    if additional_commands:
        for ac in additional_commands:
            config.subCommands.extend(ac.subCommands)
            ac_dispatch.update(ac.dispatch)

    try:
        config.parseOptions(argv)
    except usage.error, e:
        if not run_by_human:
            raise
        print "%s:  %s" % (sys.argv[0], e)
        print
        c = getattr(config, 'subOptions', config)
        print str(c)
        return 1

    command = config.subCommand
    so = config.subOptions

    if config['quiet']:
        stdout = StringIO()

    so.stdout = stdout
    so.stderr = stderr
    so.stdin = stdin

    rc = 0
    if command in create_node.dispatch:
        for basedir in so.basedirs:
            f = create_node.dispatch[command]
            rc = f(basedir, so, stdout, stderr) or rc
    elif command in startstop_node.dispatch:
        rc = startstop_node.dispatch[command](so, stdout, stderr)
    elif command in debug.dispatch:
        rc = debug.dispatch[command](so, stdout, stderr)
    elif command in cli.dispatch:
        rc = cli.dispatch[command](so)
    elif command in keygen.dispatch:
        rc = keygen.dispatch[command](so, stdout, stderr)
    elif command in ac_dispatch:
        rc = ac_dispatch[command](so, stdout, stderr)
    else:
        raise usage.UsageError()

    return rc

def run(install_node_control=True):
    rc = runner(sys.argv[1:])
    sys.exit(rc)
