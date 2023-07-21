import os, sys
from io import StringIO
from past.builtins import unicode
import six

from twisted.python import usage
from twisted.internet import defer, task, threads

from allmydata.scripts.common import get_default_nodedir
from allmydata.scripts import debug, create_node, cli, \
    admin, tahoe_run, tahoe_invite
from allmydata.scripts.types_ import SubCommands
from allmydata.util.encodingutil import quote_local_unicode_path, argv_to_unicode
from allmydata.util.eliotutil import (
    opt_eliot_destination,
    opt_help_eliot_destinations,
    eliot_logging_service,
)

from .. import (
    __full_version__,
)

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


process_control_commands : SubCommands = [
    ("run", None, tahoe_run.RunOptions, "run a node without daemonizing"),
]


class Options(usage.Options):
    """
    :ivar wormhole: An object exposing the magic-wormhole API (mainly a test
        hook).
    """
    # unit tests can override these to point at StringIO instances
    stdin = sys.stdin
    stdout = sys.stdout
    stderr = sys.stderr

    from wormhole import wormhole

    subCommands = (     create_node.subCommands
                    +   admin.subCommands
                    +   process_control_commands
                    +   debug.subCommands
                    +   cli.subCommands
                    +   tahoe_invite.subCommands
                    )

    optFlags = [
        ["quiet", "q", "Operate silently."],
        ["version", "V", "Display version numbers."],
        ["version-and-path", None, "Display version numbers and paths to their locations."],
    ]
    optParameters = [
        ["node-directory", "d", None, NODEDIR_HELP],
        ["wormhole-server", None, u"ws://wormhole.tahoe-lafs.org:4000/v1", "The magic wormhole server to use.", six.text_type],
        ["wormhole-invite-appid", None, u"tahoe-lafs.org/invite", "The appid to use on the wormhole server.", six.text_type],
    ]

    def opt_version(self):
        print(__full_version__, file=self.stdout)
        self.no_command_needed = True

    opt_version_and_path = opt_version

    opt_eliot_destination = opt_eliot_destination
    opt_help_eliot_destinations = opt_help_eliot_destinations

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
for module in (create_node,):
    create_dispatch.update(module.dispatch)  # type: ignore

def parse_options(argv, config=None):
    if not config:
        config = Options()
    try:
        config.parseOptions(argv)
    except usage.error as e:
        if six.PY2:
            # On Python 2 the exception may hold non-ascii in a byte string.
            # This makes it impossible to convert the exception to any kind of
            # string using str() or unicode().  It could also hold non-ascii
            # in a unicode string which still makes it difficult to convert it
            # to a byte string later.
            #
            # So, reach inside and turn it into some entirely safe ascii byte
            # strings that will survive being written to stdout without
            # causing too much damage in the process.
            #
            # As a result, non-ascii will not be rendered correctly but
            # instead as escape sequences.  At least this can go away when
            # we're done with Python 2 support.
            raise usage.error(*(
                arg.encode("ascii", errors="backslashreplace")
                if isinstance(arg, unicode)
                else arg.decode("utf-8").encode("ascii", errors="backslashreplace")
                for arg
                in e.args
            ))
        raise
    return config

def parse_or_exit(config, argv, stdout, stderr):
    """
    Parse Tahoe-LAFS CLI arguments and return a configuration object if they
    are valid.

    If they are invalid, write an explanation to ``stdout`` and exit.

    :param allmydata.scripts.runner.Options config: An instance of the
        argument-parsing class to use.

    :param [unicode] argv: The argument list to parse, including the name of the
        program being run as ``argv[0]``.

    :param stdout: The file-like object to use as stdout.
    :param stderr: The file-like object to use as stderr.

    :raise SystemExit: If there is an argument-parsing problem.

    :return: ``config``, after using it to parse the argument list.
    """
    try:
        config.stdout = stdout
        config.stderr = stderr
        parse_options(argv[1:], config=config)
    except usage.error as e:
        # `parse_options` may have the side-effect of initializing a
        # "sub-option" of the given configuration, even if it ultimately
        # raises an exception.  For example, `tahoe run --invalid-option` will
        # set `config.subOptions` to an instance of
        # `allmydata.scripts.tahoe_run.RunOptions` and then raise a
        # `usage.error` because `RunOptions` does not recognize
        # `--invalid-option`.  If `run` itself had a sub-options then the same
        # thing could happen but with another layer of nesting.  We can
        # present the user with the most precise information about their usage
        # error possible by finding the most "sub" of the sub-options and then
        # showing that to the user along with the usage error.
        c = config
        while hasattr(c, 'subOptions'):
            c = c.subOptions
        print(str(c), file=stdout)
        exc_str = str(e)
        exc_bytes = six.ensure_binary(exc_str, "utf-8")
        msg_bytes = b"%s:  %s\n" % (six.ensure_binary(argv[0]), exc_bytes)
        print(six.ensure_text(msg_bytes, "utf-8"), file=stdout)
        sys.exit(1)
    return config

def dispatch(config,
             reactor,
             stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr):
    command = config.subCommand
    so = config.subOptions
    if config['quiet']:
        stdout = StringIO()
    so.stdout = stdout
    so.stderr = stderr
    so.stdin = stdin
    config.stdin = stdin

    if command in create_dispatch:
        f = create_dispatch[command]
    elif command == "run":
        f = lambda config: tahoe_run.run(reactor, config)
    elif command in debug.dispatch:
        f = debug.dispatch[command]
    elif command in admin.dispatch:
        f = admin.dispatch[command]
    elif command in cli.dispatch:
        # these are blocking, and must be run in a thread
        f0 = cli.dispatch[command]
        f = lambda so: threads.deferToThread(f0, so)
    elif command in tahoe_invite.dispatch:
        f = tahoe_invite.dispatch[command]
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

def _maybe_enable_eliot_logging(options, reactor):
    if options.get("destinations"):
        service = eliot_logging_service(reactor, options["destinations"])
        # There is no Twisted "Application" around to hang this on so start
        # and stop it ourselves.
        service.startService()
        reactor.addSystemEventTrigger("after", "shutdown", service.stopService)
    # Pass on the options so we can dispatch the subcommand.
    return options


def run(configFactory=Options, argv=sys.argv, stdout=sys.stdout, stderr=sys.stderr):
    """
    Run a Tahoe-LAFS node.

    :param configFactory: A zero-argument callable which creates the config
        object to use to parse the argument list.

    :param [str] argv: The argument list to use to configure the run.

    :param stdout: The file-like object to use for stdout.
    :param stderr: The file-like object to use for stderr.

    :raise SystemExit: Always raised after the run is complete.
    """
    if sys.platform == "win32":
        from allmydata.windows.fixups import initialize
        initialize()
    # doesn't return: calls sys.exit(rc)
    task.react(
        lambda reactor: _run_with_reactor(
            reactor,
            configFactory(),
            argv,
            stdout,
            stderr,
        ),
    )


def _setup_coverage(reactor, argv):
    """
    If coverage measurement was requested, start collecting coverage
    measurements and arrange to record those measurements when the process is
    done.

    Coverage measurement is considered requested if ``"--coverage"`` is in
    ``argv`` (and it will be removed from ``argv`` if it is found).  There
    should be a ``.coveragerc`` file in the working directory if coverage
    measurement is requested.

    This is only necessary to support multi-process coverage measurement,
    typically when the test suite is running, and with the pytest-based
    *integration* test suite (at ``integration/`` in the root of the source
    tree) foremost in mind.  The idea is that if you are running Tahoe-LAFS in
    a configuration where multiple processes are involved - for example, a
    test process and a client node process, if you only measure coverage from
    the test process then you will fail to observe most Tahoe-LAFS code that
    is being run.

    This function arranges to have any Tahoe-LAFS process (such as that
    client node process) collect and report coverage measurements as well.
    """
    # can we put this _setup_coverage call after we hit
    # argument-parsing?
    # ensure_str() only necessary on Python 2.
    if six.ensure_str('--coverage') not in sys.argv:
        return
    argv.remove('--coverage')

    try:
        import coverage
    except ImportError:
        raise RuntimeError(
                "The 'coveage' package must be installed to use --coverage"
        )

    # this doesn't change the shell's notion of the environment, but
    # it makes the test in process_startup() succeed, which is the
    # goal here.
    os.environ["COVERAGE_PROCESS_START"] = '.coveragerc'

    # maybe-start the global coverage, unless it already got started
    cov = coverage.process_startup()
    if cov is None:
        cov = coverage.process_startup.coverage

    def write_coverage_data():
        """
        Make sure that coverage has stopped; internally, it depends on
        ataxit handlers running which doesn't always happen (Twisted's
        shutdown hook also won't run if os._exit() is called, but it
        runs more-often than atexit handlers).
        """
        cov.stop()
        cov.save()
    reactor.addSystemEventTrigger('after', 'shutdown', write_coverage_data)


def _run_with_reactor(reactor, config, argv, stdout, stderr):
    """
    Run a Tahoe-LAFS node using the given reactor.

    :param reactor: The reactor to use.  This implementation largely ignores
        this and lets the rest of the implementation pick its own reactor.
        Oops.

    :param twisted.python.usage.Options config: The config object to use to
        parse the argument list.

    :param [str] argv: The argument list to parse, *excluding* the name of the
        program being run.

    :param stdout: See ``run``.
    :param stderr: See ``run``.

    :return: A ``Deferred`` that fires when the run is complete.
    """
    _setup_coverage(reactor, argv)

    argv = list(map(argv_to_unicode, argv))
    d = defer.maybeDeferred(
        parse_or_exit,
        config,
        argv,
        stdout,
        stderr,
    )
    d.addCallback(_maybe_enable_eliot_logging, reactor)
    d.addCallback(dispatch, reactor, stdout=stdout, stderr=stderr)
    def _show_exception(f):
        # when task.react() notices a non-SystemExit exception, it does
        # log.err() with the failure and then exits with rc=1. We want this
        # to actually print the exception to stderr, like it would do if we
        # weren't using react().
        if f.check(SystemExit):
            return f # dispatch function handled it
        f.printTraceback(file=stderr)
        sys.exit(1)
    d.addErrback(_show_exception)
    return d

if __name__ == "__main__":
    run()
