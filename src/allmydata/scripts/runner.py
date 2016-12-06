
import os, sys
from cStringIO import StringIO

from twisted.python import usage
from twisted.internet import defer, task, threads

from allmydata.scripts.common import get_default_nodedir
from allmydata.scripts import debug, create_node, startstop_node, cli, \
    stats_gatherer, admin, magic_folder_cli
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
for module in (create_node, stats_gatherer):
    create_dispatch.update(module.dispatch)

def parse_options(argv, config=None):
    if not config:
        config = Options()
    config.parseOptions(argv) # may raise usage.error
    return config

def parse_or_exit_with_explanation(argv, stdout=sys.stdout):
    config = Options()
    try:
        parse_options(argv, config=config)
    except usage.error, e:
        c = config
        while hasattr(c, 'subOptions'):
            c = c.subOptions
        print >>stdout, str(c)
        try:
            msg = e.args[0].decode(get_io_encoding())
        except Exception:
            msg = repr(e)
        print >>stdout, "%s:  %s\n" % (sys.argv[0], quote_output(msg, quotemarks=False))
        sys.exit(1)
    return config

def dispatch(config,
             stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr):
    command = config.subCommand
    so = config.subOptions
    if config['quiet']:
        stdout = StringIO()
    so.stdout = stdout
    so.stderr = stderr
    so.stdin = stdin

    if command in create_dispatch:
        f = create_dispatch[command]
    elif command in startstop_node.dispatch:
        f = startstop_node.dispatch[command]
    elif command in debug.dispatch:
        f = debug.dispatch[command]
    elif command in admin.dispatch:
        f = admin.dispatch[command]
    elif command in cli.dispatch:
        # these are blocking, and must be run in a thread
        f0 = cli.dispatch[command]
        f = lambda so: threads.deferToThread(f0, so)
    elif command in magic_folder_cli.dispatch:
        # same
        f0 = magic_folder_cli.dispatch[command]
        f = lambda so: threads.deferToThread(f0, so)
    else:
        raise usage.UsageError()

    d = defer.maybeDeferred(f, so)
    # the calling convention for CLI dispatch functions is that they either:
    # 1: succeed and return rc=0
    # 2: print explanation to stderr and return rc!=0
    # 3: raise an exception that should just be printed normally
    # 4: return a Deferred that does 1 or 2 or 3
    def _raise_sys_exit(rc):
        sys.exit(rc)
    d.addCallback(_raise_sys_exit)
    return d

def run():
    assert sys.version_info < (3,), ur"Tahoe-LAFS does not run under Python 3. Please use Python 2.7.x."

    if sys.platform == "win32":
        from allmydata.windows.fixups import initialize
        initialize()
    d = defer.maybeDeferred(parse_or_exit_with_explanation, sys.argv[1:])
    d.addCallback(dispatch)
    def _show_exception(f):
        # when task.react() notices a non-SystemExit exception, it does
        # log.err() with the failure and then exits with rc=1. We want this
        # to actually print the exception to stderr, like it would do if we
        # weren't using react().
        if f.check(SystemExit):
            return f # dispatch function handled it
        f.printTraceback(file=sys.stderr)
        sys.exit(1)
    d.addErrback(_show_exception)
    task.react(lambda _reactor: d) # doesn't return: calls sys.exit(rc)

if __name__ == "__main__":
    run()
