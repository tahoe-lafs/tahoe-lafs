"""
Ported to Python 3.
"""
from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

__all__ = [
    "RunOptions",
    "run",
]

import os, sys
from allmydata.scripts.common import BasedirOptions
from twisted.scripts import twistd
from twisted.python import usage
from twisted.python.filepath import FilePath
from twisted.python.reflect import namedAny
from twisted.python.failure import Failure
from twisted.internet.defer import maybeDeferred, Deferred
from twisted.internet.protocol import Protocol
from twisted.internet.stdio import StandardIO
from twisted.internet.error import ReactorNotRunning
from twisted.application.service import Service

from allmydata.scripts.default_nodedir import _default_nodedir
from allmydata.util.encodingutil import listdir_unicode, quote_local_unicode_path
from allmydata.util.configutil import UnknownConfigError
from allmydata.util.deferredutil import HookMixin
from allmydata.util.pid import (
    parse_pidfile,
    check_pid_process,
    cleanup_pidfile,
    ProcessInTheWay,
    InvalidPidFile,
)
from allmydata.storage.crawler import (
    MigratePickleFileError,
)
from allmydata.storage_client import (
    MissingPlugin,
)
from allmydata.node import (
    PortAssignmentRequired,
    PrivacyError,
)


def get_pidfile(basedir):
    """
    Returns the path to the PID file.
    :param basedir: the node's base directory
    :returns: the path to the PID file
    """
    return os.path.join(basedir, u"running.process")


def get_pid_from_pidfile(pidfile):
    """
    Tries to read and return the PID stored in the node's PID file

    :param pidfile: try to read this PID file
    :returns: A numeric PID on success, ``None`` if PID file absent or
              inaccessible, ``-1`` if PID file invalid.
    """
    try:
        pid, _ = parse_pidfile(pidfile)
    except EnvironmentError:
        return None
    except InvalidPidFile:
        return -1

    return pid


def identify_node_type(basedir):
    """
    :return unicode: None or one of: 'client' or 'introducer'.
    """
    tac = u''
    try:
        for fn in listdir_unicode(basedir):
            if fn.endswith(u".tac"):
                tac = fn
                break
    except OSError:
        return None

    for t in (u"client", u"introducer"):
        if t in tac:
            return t
    return None


class RunOptions(BasedirOptions):
    subcommand_name = "run"

    optParameters = [
        ("basedir", "C", None,
         "Specify which Tahoe base directory should be used."
         " This has the same effect as the global --node-directory option."
         " [default: %s]" % quote_local_unicode_path(_default_nodedir)),
        ]

    optFlags = [
        ("allow-stdin-close", None,
         'Do not exit when stdin closes ("tahoe run" otherwise will exit).'),
    ]

    def parseArgs(self, basedir=None, *twistd_args):
        # This can't handle e.g. 'tahoe run --reactor=foo', since
        # '--reactor=foo' looks like an option to the tahoe subcommand, not to
        # twistd. So you can either use 'tahoe run' or 'tahoe run NODEDIR
        # --TWISTD-OPTIONS'. Note that 'tahoe --node-directory=NODEDIR run
        # --TWISTD-OPTIONS' also isn't allowed, unfortunately.

        BasedirOptions.parseArgs(self, basedir)
        self.twistd_args = twistd_args

    def getSynopsis(self):
        return ("Usage:  %s [global-options] %s [options]"
                " [NODEDIR [twistd-options]]"
                % (self.command_name, self.subcommand_name))

    def getUsage(self, width=None):
        t = BasedirOptions.getUsage(self, width) + "\n"
        twistd_options = str(MyTwistdConfig()).partition("\n")[2].partition("\n\n")[0]
        t += twistd_options.replace("Options:", "twistd-options:", 1)
        t += """

Note that if any twistd-options are used, NODEDIR must be specified explicitly
(not by default or using -C/--basedir or -d/--node-directory), and followed by
the twistd-options.
"""
        return t


class MyTwistdConfig(twistd.ServerOptions):
    subCommands = [("DaemonizeTahoeNode", None, usage.Options, "node")]

    stderr = sys.stderr


class DaemonizeTheRealService(Service, HookMixin):
    """
    this HookMixin should really be a helper; our hooks:

    - 'running': triggered when startup has completed; it triggers
        with None of successful or a Failure otherwise.
    """
    stderr = sys.stderr

    def __init__(self, nodetype, basedir, options):
        super(DaemonizeTheRealService, self).__init__()
        self.nodetype = nodetype
        self.basedir = basedir
        # setup for HookMixin
        self._hooks = {
            "running": None,
        }
        self.stderr = options.parent.stderr
        self._close_on_stdin_close = False if options["allow-stdin-close"] else True

    def startService(self):

        from twisted.internet import reactor

        def start():
            node_to_instance = {
                u"client": lambda: maybeDeferred(namedAny("allmydata.client.create_client"), self.basedir),
                u"introducer": lambda: maybeDeferred(namedAny("allmydata.introducer.server.create_introducer"), self.basedir),
            }

            try:
                service_factory = node_to_instance[self.nodetype]
            except KeyError:
                raise ValueError("unknown nodetype %s" % self.nodetype)

            def handle_config_error(reason):
                if reason.check(UnknownConfigError):
                    self.stderr.write("\nConfiguration error:\n{}\n\n".format(reason.value))
                elif reason.check(PortAssignmentRequired):
                    self.stderr.write("\ntub.port cannot be 0: you must choose.\n\n")
                elif reason.check(PrivacyError):
                    self.stderr.write("\n{}\n\n".format(reason.value))
                elif reason.check(MigratePickleFileError):
                    self.stderr.write(
                        "Error\nAt least one 'pickle' format file exists.\n"
                        "The file is {}\n"
                        "You must either delete the pickle-format files"
                        " or migrate them using the command:\n"
                        "    tahoe admin migrate-crawler --basedir {}\n\n"
                        .format(
                            reason.value.args[0].path,
                            self.basedir,
                        )
                    )
                elif reason.check(MissingPlugin):
                    self.stderr.write(
                        "Missing Plugin\n"
                        "The configuration requests a plugin:\n"
                        "\n    {}\n\n"
                        "...which cannot be found.\n"
                        "This typically means that some software hasn't been installed or the plugin couldn't be instantiated.\n\n"
                        .format(
                            reason.value.plugin_name,
                        )
                    )
                else:
                    self.stderr.write("\nUnknown error, here's the traceback:\n")
                    reason.printTraceback(self.stderr)
                reactor.stop()

            d = service_factory()

            def created(srv):
                if self.parent is not None:
                    srv.setServiceParent(self.parent)
                # exiting on stdin-closed facilitates cleanup when run
                # as a subprocess
                if self._close_on_stdin_close:
                    on_stdin_close(reactor, reactor.stop)
            d.addCallback(created)
            d.addErrback(handle_config_error)
            d.addBoth(self._call_hook, 'running')
            return d

        reactor.callWhenRunning(start)


class DaemonizeTahoeNodePlugin(object):
    tapname = "tahoenode"
    def __init__(self, nodetype, basedir, allow_stdin_close):
        self.nodetype = nodetype
        self.basedir = basedir
        self.allow_stdin_close = allow_stdin_close

    def makeService(self, so):
        so["allow-stdin-close"] = self.allow_stdin_close
        return DaemonizeTheRealService(self.nodetype, self.basedir, so)


def on_stdin_close(reactor, fn):
    """
    Arrange for the function `fn` to run when our stdin closes
    """
    when_closed_d = Deferred()

    class WhenClosed(Protocol):
        """
        Notify a Deferred when our connection is lost .. as this is passed
        to twisted's StandardIO class, it is used to detect our parent
        going away.
        """

        def connectionLost(self, reason):
            when_closed_d.callback(None)

    def on_close(arg):
        try:
            fn()
        except ReactorNotRunning:
            pass
        except Exception:
            # for our "exit" use-case failures will _mostly_ just be
            # ReactorNotRunning (because we're already shutting down
            # when our stdin closes) but no matter what "bad thing"
            # happens we just want to ignore it .. although other
            # errors might be interesting so we'll log those
            print(Failure())
        return arg

    when_closed_d.addBoth(on_close)
    # we don't need to do anything with this instance because it gets
    # hooked into the reactor and thus remembered .. but we return it
    # for Windows testing purposes.
    return StandardIO(
        proto=WhenClosed(),
        reactor=reactor,
    )


def run(reactor, config, runApp=twistd.runApp):
    """
    Runs a Tahoe-LAFS node in the foreground.

    Sets up the IService instance corresponding to the type of node
    that's starting and uses Twisted's twistd runner to disconnect our
    process from the terminal.
    """
    out = config.stdout
    err = config.stderr
    basedir = config['basedir']
    quoted_basedir = quote_local_unicode_path(basedir)
    print("'tahoe {}' in {}".format(config.subcommand_name, quoted_basedir), file=out)
    if not os.path.isdir(basedir):
        print("%s does not look like a directory at all" % quoted_basedir, file=err)
        return 1
    nodetype = identify_node_type(basedir)
    if not nodetype:
        print("%s is not a recognizable node directory" % quoted_basedir, file=err)
        return 1

    twistd_args = [
        # ensure twistd machinery does not daemonize.
        "--nodaemon",
        "--rundir", basedir,
    ]
    if sys.platform != "win32":
        # turn off Twisted's pid-file to use our own -- but not on
        # windows, because twistd doesn't know about pidfiles there
        twistd_args.extend(["--pidfile", None])
    twistd_args.extend(config.twistd_args)
    twistd_args.append("DaemonizeTahoeNode") # point at our DaemonizeTahoeNodePlugin

    twistd_config = MyTwistdConfig()
    twistd_config.stdout = out
    twistd_config.stderr = err
    try:
        twistd_config.parseOptions(twistd_args)
    except usage.error as ue:
        # these arguments were unsuitable for 'twistd'
        print(config, file=err)
        print("tahoe %s: usage error from twistd: %s\n" % (config.subcommand_name, ue), file=err)
        return 1
    twistd_config.loadedPlugins = {
        "DaemonizeTahoeNode": DaemonizeTahoeNodePlugin(nodetype, basedir, config["allow-stdin-close"])
    }

    # our own pid-style file contains PID and process creation time
    pidfile = FilePath(get_pidfile(config['basedir']))
    try:
        check_pid_process(pidfile)
    except (ProcessInTheWay, InvalidPidFile) as e:
        print("ERROR: {}".format(e), file=err)
        return 1
    else:
        reactor.addSystemEventTrigger(
            "after", "shutdown",
            lambda: cleanup_pidfile(pidfile)
        )

    # We always pass --nodaemon so twistd.runApp does not daemonize.
    print("running node in %s" % (quoted_basedir,), file=out)
    runApp(twistd_config)
    return 0
