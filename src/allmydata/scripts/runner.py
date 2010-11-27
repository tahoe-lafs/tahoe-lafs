
import sys
from cStringIO import StringIO

from twisted.python import usage

from allmydata.scripts.common import BaseOptions
from allmydata.scripts import debug, create_node, startstop_node, cli, keygen, stats_gatherer
from allmydata.util.encodingutil import quote_output, get_argv_encoding

def GROUP(s):
    # Usage.parseOptions compares argv[1] against command[0], so it will
    # effectively ignore any "subcommand" that starts with a newline. We use
    # these to insert section headers into the --help output.
    return [("\n" + s, None, None, None)]


class Options(BaseOptions, usage.Options):
    synopsis = "\nUsage:  tahoe <command> [command options]"
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


create_dispatch = {}
for module in (create_node, keygen, stats_gatherer):
    create_dispatch.update(module.dispatch)

def runner(argv,
           run_by_human=True,
           stdin=None, stdout=None, stderr=None,
           install_node_control=True, additional_commands=None):

    stdin  = stdin  or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

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
        print >>stdout, str(c)
        try:
            msg = e.args[0].decode(get_argv_encoding())
        except Exception:
            msg = repr(e)
        print >>stdout, "%s:  %s\n" % (sys.argv[0], quote_output(msg, quotemarks=False))
        return 1

    command = config.subCommand
    so = config.subOptions

    if config['quiet']:
        stdout = StringIO()

    so.stdout = stdout
    so.stderr = stderr
    so.stdin = stdin

    if command in create_dispatch:
        rc = create_dispatch[command](so, stdout, stderr)
    elif command in startstop_node.dispatch:
        rc = startstop_node.dispatch[command](so, stdout, stderr)
    elif command in debug.dispatch:
        rc = debug.dispatch[command](so)
    elif command in cli.dispatch:
        rc = cli.dispatch[command](so)
    elif command in ac_dispatch:
        rc = ac_dispatch[command](so, stdout, stderr)
    else:
        raise usage.UsageError()

    return rc


def run(install_node_control=True):
    if sys.platform == "win32":
        from allmydata.windows.fixups import initialize
        initialize()

    rc = runner(sys.argv[1:], install_node_control=install_node_control)
    sys.exit(rc)
