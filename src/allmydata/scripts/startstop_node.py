
import os, sys, signal, time
from allmydata.scripts.common import BasedirOptions
from twisted.scripts import twistd
from twisted.python import usage
from allmydata.util import fileutil
from allmydata.util.encodingutil import listdir_unicode, quote_output


class StartOptions(BasedirOptions):
    def parseArgs(self, basedir=None, *twistd_args):
        # this can't handle e.g. 'tahoe start --nodaemon', since then
        # --nodaemon looks like a basedir. So you can either use 'tahoe
        # start' or 'tahoe start BASEDIR --TWISTD-OPTIONS'.
        BasedirOptions.parseArgs(self, basedir)
        self.twistd_args = twistd_args

    def getSynopsis(self):
        return "Usage:  %s [global-opts] start [options] [NODEDIR]" % (self.command_name,)


class StopOptions(BasedirOptions):
    def getSynopsis(self):
        return "Usage:  %s [global-opts] stop [options] [NODEDIR]" % (self.command_name,)


class RestartOptions(StartOptions):
    def getSynopsis(self):
        return "Usage:  %s [global-opts] restart [options] [NODEDIR]" % (self.command_name,)


class RunOptions(StartOptions):
    def getSynopsis(self):
        return "Usage:  %s [global-opts] run [options] [NODEDIR]" % (self.command_name,)


class MyTwistdConfig(twistd.ServerOptions):
    subCommands = [("XYZ", None, usage.Options, "node")]

class NodeStartingPlugin:
    tapname = "xyznode"
    def __init__(self, nodetype, basedir):
        self.nodetype = nodetype
        self.basedir = basedir
    def makeService(self, so):
        # delay this import as late as possible, to allow twistd's code to
        # accept --reactor= selection. N.B.: this can't actually work until
        # this file, and all the __init__.py files above it, also respect the
        # prohibition on importing anything that transitively imports
        # twisted.internet.reactor . That will take a lot of work.
        if self.nodetype == "client":
            from allmydata.client import Client
            return Client(self.basedir)
        if self.nodetype == "introducer":
            from allmydata.introducer.server import IntroducerNode
            return IntroducerNode(self.basedir)
        if self.nodetype == "key-generator":
            from allmydata.key_generator import KeyGeneratorService
            return KeyGeneratorService(default_key_size=2048)
        if self.nodetype == "stats-gatherer":
            from allmydata.stats import StatsGathererService
            return StatsGathererService(verbose=True)
        raise ValueError("unknown nodetype %s" % self.nodetype)

def identify_node_type(basedir):
    for fn in listdir_unicode(basedir):
        if fn.endswith(u".tac"):
            tac = str(fn)
            break
    else:
        return None

    for t in ("client", "introducer", "key-generator", "stats-gatherer"):
        if t in tac:
            return t
    return None

def start(config, out=sys.stdout, err=sys.stderr):
    basedir = config['basedir']
    print >>out, "STARTING", quote_output(basedir)
    if not os.path.isdir(basedir):
        print >>err, "%s does not look like a directory at all" % quote_output(basedir)
        return 1
    nodetype = identify_node_type(basedir)
    if not nodetype:
        print >>err, "%s is not a recognizable node directory" % quote_output(basedir)
        return 1
    # Now prepare to turn into a twistd process. This os.chdir is the point
    # of no return.
    os.chdir(basedir)
    twistd_args = []
    if (nodetype in ("client", "introducer")
        and "--nodaemon" not in config.twistd_args
        and "--syslog" not in config.twistd_args
        and "--logfile" not in config.twistd_args):
        fileutil.make_dirs(os.path.join(basedir, "logs"))
        twistd_args.extend(["--logfile", os.path.join("logs", "twistd.log")])
    twistd_args.extend(config.twistd_args)
    twistd_args.append("XYZ") # point at our NodeStartingPlugin

    twistd_config = MyTwistdConfig()
    try:
        twistd_config.parseOptions(twistd_args)
    except usage.error, ue:
        # these arguments were unsuitable for 'twistd'
        print >>err, twistd_config
        print >>err, "tahoe start: %s" % (config.subCommand, ue)
        return 1
    twistd_config.loadedPlugins = {"XYZ": NodeStartingPlugin(nodetype, basedir)}

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

    print >>out, "%s node in %s" % (verb, basedir)
    twistd.runApp(twistd_config)
    # we should only reach here if --nodaemon or equivalent was used
    return 0

def stop(config, out=sys.stdout, err=sys.stderr):
    basedir = config['basedir']
    print >>out, "STOPPING", quote_output(basedir)
    pidfile = os.path.join(basedir, "twistd.pid")
    if not os.path.exists(pidfile):
        print >>err, "%s does not look like a running node directory (no twistd.pid)" % quote_output(basedir)
        # we define rc=2 to mean "nothing is running, but it wasn't me who
        # stopped it"
        return 2
    pid = open(pidfile, "r").read()
    pid = int(pid)

    # kill it hard (SIGKILL), delete the twistd.pid file, then wait for the
    # process itself to go away. If it hasn't gone away after 20 seconds, warn
    # the user but keep waiting until they give up.
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError, oserr:
        if oserr.errno == 3:
            print oserr.strerror
            # the process didn't exist, so wipe the pid file
            os.remove(pidfile)
            return 2
        else:
            raise
    try:
        os.remove(pidfile)
    except EnvironmentError:
        pass
    start = time.time()
    time.sleep(0.1)
    wait = 40
    first_time = True
    while True:
        # poll once per second until we see the process is no longer running
        try:
            os.kill(pid, 0)
        except OSError:
            print >>out, "process %d is dead" % pid
            return
        wait -= 1
        if wait < 0:
            if first_time:
                print >>err, ("It looks like pid %d is still running "
                              "after %d seconds" % (pid,
                                                    (time.time() - start)))
                print >>err, "I will keep watching it until you interrupt me."
                wait = 10
                first_time = False
            else:
                print >>err, "pid %d still running after %d seconds" % \
                      (pid, (time.time() - start))
                wait = 10
        time.sleep(1)
    # we define rc=1 to mean "I think something is still running, sorry"
    return 1

def restart(config, stdout, stderr):
    rc = stop(config, stdout, stderr)
    if rc == 2:
        print >>stderr, "ignoring couldn't-stop"
        rc = 0
    if rc:
        print >>stderr, "not restarting"
        return rc
    return start(config, stdout, stderr)

def run(config, stdout, stderr):
    config.twistd_args = config.twistd_args + ("--nodaemon",)
    # Previously we would do the equivalent of adding ("--logfile", "tahoesvc.log"),
    # but that redirects stdout/stderr which is often unhelpful, and the user can
    # add that option explicitly if they want.

    return start(config, stdout, stderr)


subCommands = [
    ["start", None, StartOptions, "Start a node (of any type)."],
    ["stop", None, StopOptions, "Stop a node."],
    ["restart", None, RestartOptions, "Restart a node."],
    ["run", None, RunOptions, "Run a node synchronously."],
]

dispatch = {
    "start": start,
    "stop": stop,
    "restart": restart,
    "run": run,
    }
