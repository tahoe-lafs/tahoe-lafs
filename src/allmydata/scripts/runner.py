
import sys
from cStringIO import StringIO

import pkg_resources
pkg_resources.require('twisted')
from twisted.python import usage

import allmydata
pkg_resources.require(allmydata.__appname__)
from allmydata.scripts.common import BaseOptions
from allmydata.scripts import debug, create_node, startstop_node, cli, keygen, stats_gatherer

def GROUP(s):
    # Usage.parseOptions compares argv[1] against command[0], so it will
    # effectively ignore any "subcommand" that starts with a newline. We use
    # these to insert section headers into the --help output.
    return [("\n" + s, None, None, None)]


class Options(BaseOptions, usage.Options):
    synopsis = "Usage:  tahoe <command> [command options]"
    subCommands = ( GROUP("Administration")
                    +   create_node.subCommands
                    +   keygen.subCommands
                    +   stats_gatherer.subCommands
                    + GROUP("Controlling a node")
                    +   startstop_node.subCommands
                    + GROUP("Debugging")
                    +   debug.subCommands
                    + GROUP("Using the filesystem")
                    +   cli.subCommands
                    )

    def getUsage(self, **kwargs):
        t = usage.Options.getUsage(self, **kwargs)
        return t + "\nPlease run 'tahoe <command> --help' for more details on each command.\n"

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
        c = config
        while hasattr(c, 'subOptions'):
            c = c.subOptions
        print str(c)
        print "%s:  %s" % (sys.argv[0], e)
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
        rc = debug.dispatch[command](so)
    elif command in cli.dispatch:
        rc = cli.dispatch[command](so)
    elif command in keygen.dispatch:
        rc = keygen.dispatch[command](so, stdout, stderr)
    elif command in stats_gatherer.dispatch:
        rc = stats_gatherer.dispatch[command](so)
    elif command in ac_dispatch:
        rc = ac_dispatch[command](so, stdout, stderr)
    else:
        raise usage.UsageError()

    return rc

def run(install_node_control=True):
    rc = runner(sys.argv[1:])
    sys.exit(rc)
