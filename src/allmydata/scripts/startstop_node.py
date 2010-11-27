
import os, sys, signal, time
from allmydata.scripts.common import BasedirMixin, BaseOptions
from allmydata.util import fileutil
from allmydata.util.assertutil import precondition
from allmydata.util.encodingutil import listdir_unicode, quote_output

class StartOptions(BasedirMixin, BaseOptions):
    optFlags = [
        ["profile", "p", "Run under the Python profiler, putting results in 'profiling_results.prof'."],
        ["syslog", None, "Tell the node to log to syslog, not a file."],
        ]

class StopOptions(BasedirMixin, BaseOptions):
    pass

class RestartOptions(BasedirMixin, BaseOptions):
    optFlags = [
        ["profile", "p", "Run under the Python profiler, putting results in 'profiling_results.prof'."],
        ["syslog", None, "Tell the node to log to syslog, not a file."],
        ]

class RunOptions(BasedirMixin, BaseOptions):
    default_nodedir = u"."
    allow_multiple = False

    optParameters = [
        ["node-directory", "d", None, "Specify the directory of the node to be run. [default, for 'tahoe run' only: current directory]"],
        ["multiple", "m", None, "['tahoe run' cannot accept multiple node directories]"],
    ]

def do_start(basedir, opts, out=sys.stdout, err=sys.stderr):
    print >>out, "STARTING", quote_output(basedir)
    if not os.path.isdir(basedir):
        print >>err, "%s does not look like a directory at all" % quote_output(basedir)
        return 1
    for fn in listdir_unicode(basedir):
        if fn.endswith(u".tac"):
            tac = str(fn)
            break
    else:
        print >>err, "%s does not look like a node directory (no .tac file)" % quote_output(basedir)
        return 1
    if "client" in tac:
        nodetype = "client"
    elif "introducer" in tac:
        nodetype = "introducer"
    else:
        nodetype = "unknown (%s)" % tac

    args = ["twistd", "-y", tac]
    if opts["syslog"]:
        args.append("--syslog")
    elif nodetype in ("client", "introducer"):
        fileutil.make_dirs(os.path.join(basedir, "logs"))
        args.extend(["--logfile", os.path.join("logs", "twistd.log")])
    if opts["profile"]:
        args.extend(["--profile=profiling_results.prof", "--savestats",])
    # now we're committed
    os.chdir(basedir)
    from twisted.scripts import twistd
    sys.argv = args
    twistd.run()
    # run() doesn't return: the parent does os._exit(0) in daemonize(), so
    # we'll never get here. If application setup fails (e.g. ImportError),
    # run() will raise an exception.

def do_stop(basedir, out=sys.stdout, err=sys.stderr):
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

def start(config, stdout, stderr):
    rc = 0
    for basedir in config['basedirs']:
        rc = do_start(basedir, config, stdout, stderr) or rc
    return rc

def stop(config, stdout, stderr):
    rc = 0
    for basedir in config['basedirs']:
        rc = do_stop(basedir, stdout, stderr) or rc
    return rc

def restart(config, stdout, stderr):
    rc = 0
    for basedir in config['basedirs']:
        rc = do_stop(basedir, stdout, stderr) or rc
    if rc == 2:
        print >>stderr, "ignoring couldn't-stop"
        rc = 0
    if rc:
        print >>stderr, "not restarting"
        return rc
    for basedir in config['basedirs']:
        rc = do_start(basedir, config, stdout, stderr) or rc
    return rc

def run(config, stdout, stderr):
    from twisted.internet import reactor
    from twisted.python import log, logfile
    from allmydata import client

    basedir = config['basedirs'][0]
    precondition(isinstance(basedir, unicode), basedir)

    if not os.path.isdir(basedir):
        print >>stderr, "%s does not look like a directory at all" % quote_output(basedir)
        return 1
    for fn in listdir_unicode(basedir):
        if fn.endswith(u".tac"):
            tac = str(fn)
            break
    else:
        print >>stderr, "%s does not look like a node directory (no .tac file)" % quote_output(basedir)
        return 1
    if "client" not in tac:
        print >>stderr, ("%s looks like it contains a non-client node (%s).\n"
                         "Use 'tahoe start' instead of 'tahoe run'."
                         % (quote_output(basedir), tac))
        return 1

    os.chdir(basedir)

    # set up twisted logging. this will become part of the node rsn.
    logdir = os.path.join(basedir, 'logs')
    if not os.path.exists(logdir):
        os.makedirs(logdir)
    lf = logfile.LogFile('tahoesvc.log', logdir)
    log.startLogging(lf)

    # run the node itself
    c = client.Client(basedir)
    reactor.callLater(0, c.startService) # after reactor startup
    reactor.run()

    return 0


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
