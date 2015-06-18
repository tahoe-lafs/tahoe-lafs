
import os, sys
from cStringIO import StringIO

from twisted.python import usage

from allmydata.scripts.common import get_default_nodedir
from allmydata.scripts import debug, create_node, startstop_node, cli, keygen, stats_gatherer, admin, magic_folder_cli
from allmydata.util.encodingutil import quote_output, quote_local_unicode_path, get_io_encoding

def GROUP(s):
    # Usage.parseOptions compares argv[1] against command[0], so it will
    # effectively ignore any "subcommand" that starts with a newline. We use
    # these to insert section headers into the --help output.
    return [("\n(%s)" % s, None, None, None)]


_default_nodedir = get_default_nodedir()

NODEDIR_HELP = ("Specify which Tahoe node directory should be used. The "
                "directory should either contain a full Tahoe node, or a "
                "file named node.url that points to some other Tahoe node. "
                "It should also contain a file named '"
                + os.path.join('private', 'aliases') +
                "' which contains the mapping from alias name to root "
                "dirnode URI.")
if _default_nodedir:
    NODEDIR_HELP += " [default for most commands: " + quote_local_unicode_path(_default_nodedir) + "]"

class Options(usage.Options):
    # unit tests can override these to point at StringIO instances
    stdin = sys.stdin
    stdout = sys.stdout
    stderr = sys.stderr

    synopsis = "\nUsage:  tahoe <command> [command options]"
    subCommands = ( GROUP("Administration")
                    +   create_node.subCommands
                    +   keygen.subCommands
                    +   stats_gatherer.subCommands
                    +   admin.subCommands
                    + GROUP("Controlling a node")
                    +   startstop_node.subCommands
                    + GROUP("Debugging")
                    +   debug.subCommands
                    + GROUP("Using the filesystem")
                    +   cli.subCommands
                    +   magic_folder_cli.subCommands
                    )

    optFlags = [
        ["quiet", "q", "Operate silently."],
        ["version", "V", "Display version numbers."],
        ["version-and-path", None, "Display version numbers and paths to their locations."],
    ]
    optParameters = [
        ["node-directory", "d", None, NODEDIR_HELP],
    ]

    def opt_version(self):
        import allmydata
        print >>self.stdout, allmydata.get_package_versions_string(debug=True)
        self.no_command_needed = True

    def opt_version_and_path(self):
        import allmydata
        print >>self.stdout, allmydata.get_package_versions_string(show_paths=True, debug=True)
        self.no_command_needed = True

    def __str__(self):
        return ("\nUsage: tahoe [global-options] <command> [command-options]\n"
                + self.getUsage())

    synopsis = "\nUsage: tahoe [global-options]" # used only for subcommands

    def getUsage(self, **kwargs):
        t = usage.Options.getUsage(self, **kwargs)
        t = t.replace("Options:", "\nGlobal options:", 1)
        return t + "\nPlease run 'tahoe <command> --help' for more details on each command.\n"

    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            if not hasattr(self, 'no_command_needed'):
                raise usage.UsageError("must specify a command")
            sys.exit(0)


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
            msg = e.args[0].decode(get_io_encoding())
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
    elif command in admin.dispatch:
        rc = admin.dispatch[command](so)
    elif command in cli.dispatch:
        rc = cli.dispatch[command](so)
    elif command in magic_folder_cli.dispatch:
        rc = magic_folder_cli.dispatch[command](so)
    elif command in ac_dispatch:
        rc = ac_dispatch[command](so, stdout, stderr)
    else:
        raise usage.UsageError()

    return rc


def run(install_node_control=True):
    try:
        if sys.platform == "win32":
            from allmydata.windows.fixups import initialize
            initialize()

        rc = runner(sys.argv[1:], install_node_control=install_node_control)
    except Exception:
        import traceback
        traceback.print_exc()
        rc = 1

    sys.exit(rc)
