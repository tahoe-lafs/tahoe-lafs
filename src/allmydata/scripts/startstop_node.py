
import os, sys, signal, time
from twisted.python import usage
from allmydata.scripts.common import BasedirMixin
from allmydata.util import fileutil, find_exe

class StartOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to start the node in"],
        ]
    optFlags = [
        ["profile", "p", "whether to run under the Python profiler, putting results in \"profiling_results.prof\""],
        ]

class StopOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to stop the node in"],
        ]

class RestartOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to restart the node in"],
        ]
    optFlags = [
        ["force", "f", "if the node is not already running, start it "
         "instead of complaining that you should have used 'start' instead "
         "of 'restart'"],
        ["profile", "p", "whether to run under the Python profiler, putting results in \"profiling_results.prof\""],
        ]

class RunOptions(usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to run the node in, CWD by default"],
        ]

def do_start(basedir, profile=False, out=sys.stdout, err=sys.stderr):
    print >>out, "STARTING", basedir
    if not os.path.isdir(basedir):
        print >>err, "%s does not look like a directory at all" % basedir
        return 1
    for fn in os.listdir(basedir):
        if fn.endswith(".tac"):
            tac = fn
            break
    else:
        print >>err, "%s does not look like a node directory (no .tac file)" % basedir
        return 1
    if "client" in tac:
        nodetype = "client"
    elif "introducer" in tac:
        nodetype = "introducer"
    else:
        nodetype = "unknown (%s)" % tac

    cmd = find_exe.find_exe('twistd')
    if not cmd:
        print "Can't find twistd (it comes with Twisted).  Aborting."
        sys.exit(1)

    fileutil.make_dirs(os.path.join(basedir, "logs"))
    cmd.extend(["-y", tac])
    if nodetype in ("client", "introducer"):
        cmd.extend(["--logfile", os.path.join("logs", "twistd.log")])
    if profile:
        cmd.extend(["--profile=profiling_results.prof", "--savestats",])
    curdir = os.getcwd()
    try:
        os.chdir(basedir)
        rc = os.system(' '.join(cmd))
    finally:
        os.chdir(curdir)
    if rc == 0:
        print >>out, "%s node probably started" % nodetype
        return 0
    else:
        print >>err, "%s node probably not started" % nodetype
        return 1

def do_stop(basedir, out=sys.stdout, err=sys.stderr):
    print >>out, "STOPPING", basedir
    pidfile = os.path.join(basedir, "twistd.pid")
    if not os.path.exists(pidfile):
        print >>err, "%s does not look like a running node directory (no twistd.pid)" % basedir
        return 2
    pid = open(pidfile, "r").read()
    pid = int(pid)

    # kill it hard (SIGKILL), delete the twistd.pid file, then wait for the
    # process itself to go away. If it hasn't gone away after 5 seconds, warn
    # the user but keep waiting until they give up.
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError, oserr:
        if oserr.errno == 3:
            print oserr.strerror
            # the process didn't exist, so wipe the pid file
            os.remove(pidfile)
            return 1
        else:
            raise
    try:
        os.remove(pidfile)
    except EnvironmentError:
        pass
    start = time.time()
    time.sleep(0.1)
    wait = 5
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
    return 1

def start(config, stdout, stderr):
    rc = 0
    for basedir in config['basedirs']:
        rc = do_start(basedir, config['profile'], stdout, stderr) or rc
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
    if rc == 2 and config['force']:
        print >>stderr, "ignoring couldn't-stop"
        rc = 0
    if rc:
        print >>stderr, "not restarting"
        return rc
    for basedir in config['basedirs']:
        rc = do_start(basedir, config['profile'], stdout, stderr) or rc
    return rc

def run(config, stdout, stderr):
    from twisted.internet import reactor
    from twisted.python import log, logfile
    from allmydata import client

    basedir = config['basedir']
    if basedir is None:
        basedir = '.'
    else:
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
