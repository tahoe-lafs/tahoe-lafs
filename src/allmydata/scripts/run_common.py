from __future__ import print_function

import os, sys
from allmydata.scripts.common import BasedirOptions
from twisted.scripts import twistd
from twisted.python import usage
from twisted.python.reflect import namedAny
from twisted.internet.defer import maybeDeferred, fail
from twisted.application.service import Service

from allmydata.scripts.default_nodedir import _default_nodedir
from allmydata.util import fileutil
from allmydata.node import read_config
from allmydata.util.encodingutil import listdir_unicode, quote_local_unicode_path
from allmydata.util.configutil import UnknownConfigError
from allmydata.util.deferredutil import HookMixin


def get_pidfile(basedir):
    """
    Returns the path to the PID file.
    :param basedir: the node's base directory
    :returns: the path to the PID file
    """
    return os.path.join(basedir, u"twistd.pid")

def get_pid_from_pidfile(pidfile):
    """
    Tries to read and return the PID stored in the node's PID file
    (twistd.pid).
    :param pidfile: try to read this PID file
    :returns: A numeric PID on success, ``None`` if PID file absent or
              inaccessible, ``-1`` if PID file invalid.
    """
    try:
        with open(pidfile, "r") as f:
            pid = f.read()
    except EnvironmentError:
        return None

    try:
        pid = int(pid)
    except ValueError:
        return -1

    return pid

def identify_node_type(basedir):
    """
    :return unicode: None or one of: 'client', 'introducer',
        'key-generator' or 'stats-gatherer'
    """
    tac = u''
    try:
        for fn in listdir_unicode(basedir):
            if fn.endswith(u".tac"):
                tac = fn
                break
    except OSError:
        return None

    for t in (u"client", u"introducer", u"key-generator", u"stats-gatherer"):
        if t in tac:
            return t
    return None


class RunOptions(BasedirOptions):
    optParameters = [
        ("basedir", "C", None,
         "Specify which Tahoe base directory should be used."
         " This has the same effect as the global --node-directory option."
         " [default: %s]" % quote_local_unicode_path(_default_nodedir)),
        ]

    def parseArgs(self, basedir=None, *twistd_args):
        # This can't handle e.g. 'tahoe start --nodaemon', since '--nodaemon'
        # looks like an option to the tahoe subcommand, not to twistd. So you
        # can either use 'tahoe start' or 'tahoe start NODEDIR
        # --TWISTD-OPTIONS'. Note that 'tahoe --node-directory=NODEDIR start
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

    def startService(self):

        def key_generator_removed():
            return fail(ValueError("key-generator support removed, see #2783"))

        def start():
            node_to_instance = {
                u"client": lambda: maybeDeferred(namedAny("allmydata.client.create_client"), self.basedir),
                u"introducer": lambda: maybeDeferred(namedAny("allmydata.introducer.server.create_introducer"), self.basedir),
                u"stats-gatherer": lambda: maybeDeferred(namedAny("allmydata.stats.StatsGathererService"), read_config(self.basedir, None), self.basedir, verbose=True),
                u"key-generator": key_generator_removed,
            }

            try:
                service_factory = node_to_instance[self.nodetype]
            except KeyError:
                raise ValueError("unknown nodetype %s" % self.nodetype)

            def handle_config_error(fail):
                if fail.check(UnknownConfigError):
                    self.stderr.write("\nConfiguration error:\n{}\n\n".format(fail.value))
                else:
                    self.stderr.write("\nUnknown error\n")
                    fail.printTraceback(self.stderr)
                reactor.stop()

            d = service_factory()

            def created(srv):
                srv.setServiceParent(self.parent)
            d.addCallback(created)
            d.addErrback(handle_config_error)
            d.addBoth(self._call_hook, 'running')
            return d

        from twisted.internet import reactor
        reactor.callWhenRunning(start)


class DaemonizeTahoeNodePlugin(object):
    tapname = "tahoenode"
    def __init__(self, nodetype, basedir):
        self.nodetype = nodetype
        self.basedir = basedir

    def makeService(self, so):
        return DaemonizeTheRealService(self.nodetype, self.basedir, so)


def run(config):
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
    # Now prepare to turn into a twistd process. This os.chdir is the point
    # of no return.
    os.chdir(basedir)
    twistd_args = []
    if (nodetype in (u"client", u"introducer")
        and "--nodaemon" not in config.twistd_args
        and "--syslog" not in config.twistd_args
        and "--logfile" not in config.twistd_args):
        fileutil.make_dirs(os.path.join(basedir, u"logs"))
        twistd_args.extend(["--logfile", os.path.join("logs", "twistd.log")])
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
    twistd_config.loadedPlugins = {"DaemonizeTahoeNode": DaemonizeTahoeNodePlugin(nodetype, basedir)}

    # handle invalid PID file (twistd might not start otherwise)
    pidfile = get_pidfile(basedir)
    if get_pid_from_pidfile(pidfile) == -1:
        print("found invalid PID file in %s - deleting it" % basedir, file=err)
        os.remove(pidfile)

    # On Unix-like platforms:
    #   Unless --nodaemon was provided, the twistd.runApp() below spawns off a
    #   child process, and the parent calls os._exit(0), so there's no way for
    #   us to get control afterwards, even with 'except SystemExit'. If
    #   application setup fails (e.g. ImportError), runApp() will raise an
    #   exception.
    #
    #   So if we wanted to do anything with the running child, we'd have two
    #   options:
    #
    #    * fork first, and have our child wait for the runApp() child to get
    #      running. (note: just fork(). This is easier than fork+exec, since we
    #      don't have to get PATH and PYTHONPATH set up, since we're not
    #      starting a *different* process, just cloning a new instance of the
    #      current process)
    #    * or have the user run a separate command some time after this one
    #      exits.
    #
    #   For Tahoe, we don't need to do anything with the child, so we can just
    #   let it exit.
    #
    # On Windows:
    #   twistd does not fork; it just runs in the current process whether or not
    #   --nodaemon is specified. (As on Unix, --nodaemon does have the side effect
    #   of causing us to log to stdout/stderr.)

    if "--nodaemon" in twistd_args or sys.platform == "win32":
        verb = "running"
    else:
        verb = "starting"

    print("%s node in %s" % (verb, quoted_basedir), file=out)
    twistd.runApp(twistd_config)
    # we should only reach here if --nodaemon or equivalent was used
    return 0
