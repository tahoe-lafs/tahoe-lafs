
import os, sys, signal, time
from twisted.python import usage
from allmydata.scripts.common import BasedirMixin
from allmydata.util import fileutil
from twisted.python.procutils import which

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

def do_start(basedir, profile=False, out=sys.stdout, err=sys.stderr):
    print >>out, "STARTING", basedir
    if os.path.exists(os.path.join(basedir, "client.tac")):
        tac = "client.tac"
        type = "client"
    elif os.path.exists(os.path.join(basedir, "introducer.tac")):
        tac = "introducer.tac"
        type = "introducer"
    else:
        print >>err, "%s does not look like a node directory" % basedir
        if not os.path.isdir(basedir):
            print >>err, " in fact, it doesn't look like a directory at all!"
        return 1
    twistds = which("twistd")
    twistd = twistds and twistds[0]
    if not twistd:
        twistd = os.path.join(sys.prefix, 'Scripts', 'twistd.py') 
    if not os.path.exists(twistd):
        print "Can't find twistd (it comes with Twisted).  Aborting."
        sys.exit(1)
    path, ext = os.path.splitext(twistd)
    if ext.lower() in [".exe", ".bat",]:
        cmd = [twistd,]
    else:
        cmd = [sys.executable, twistd,]
    
    fileutil.make_dirs(os.path.join(basedir, "logs"))
    cmd.extend(["-y", tac, "--logfile", os.path.join("logs", "twistd.log")])
    if profile:
        cmd.extend(["--profile=profiling_results.prof", "--savestats",])
    curdir = os.getcwd()
    try:
        os.chdir(basedir)
        rc = os.system(' '.join(cmd))
    finally:
        os.chdir(curdir)
    if rc == 0:
        print >>out, "%s node probably started" % type
        return 0
    else:
        print >>err, "%s node probably not started" % type
        return 1

def do_stop(basedir, out=sys.stdout, err=sys.stderr):
    print >>out, "STOPPING", basedir
    pidfile = os.path.join(basedir, "twistd.pid")
    if not os.path.exists(pidfile):
        print >>err, "%s does not look like a running node directory (no twistd.pid)" % basedir
        return 2
    pid = open(pidfile, "r").read()
    pid = int(pid)

    timer = 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError, oserr:
        if oserr.errno == 3:
            print oserr.strerror
            return 1
        else:
            raise
    time.sleep(0.1)
    while timer < 5:
        # poll once per second until twistd.pid goes away, up to 5 seconds
        try:
            os.kill(pid, 0)
        except OSError:
            print >>out, "process %d is dead" % pid
            return
        timer += 1
        time.sleep(1)
    print >>err, "never saw process go away"
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


subCommands = [
    ["start", None, StartOptions, "Start a node (of any type)."],
    ["stop", None, StopOptions, "Stop a node."],
    ["restart", None, RestartOptions, "Restart a node."],
]

dispatch = {
    "start": start,
    "stop": stop,
    "restart": restart,
    }
