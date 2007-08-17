
import sys
from cStringIO import StringIO
from twisted.python import usage

from allmydata.scripts.common import BaseOptions
import debug, create_node, startstop_node, cli

class Options(BaseOptions, usage.Options):
    synopsis = "Usage:  allmydata <command> [command options]"

    subCommands = []
    subCommands += create_node.subCommands
    subCommands += startstop_node.subCommands
    subCommands += debug.subCommands
    subCommands += cli.subCommands

    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a command")

def runner(argv, run_by_human=True, stdout=sys.stdout, stderr=sys.stderr):
    config = Options()
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
        rc = cli.dispatch[command](so, stdout, stderr)
    else:
        raise usage.UsageError()

    return rc

def run():
    rc = runner(sys.argv[1:])
    sys.exit(rc)
