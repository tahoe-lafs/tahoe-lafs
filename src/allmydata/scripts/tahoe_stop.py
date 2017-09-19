import os
import time
import signal

from allmydata.scripts.common import BasedirOptions
from allmydata.util.encodingutil import quote_local_unicode_path

COULD_NOT_STOP = 2


class StopOptions(BasedirOptions):
    def parseArgs(self, basedir=None):
        BasedirOptions.parseArgs(self, basedir)

    def getSynopsis(self):
        return ("Usage:  %s [global-options] stop [options] [NODEDIR]"
                % (self.command_name,))


def stop(config):
    out = config.stdout
    err = config.stderr
    basedir = config['basedir']
    quoted_basedir = quote_local_unicode_path(basedir)
    print >>out, "STOPPING", quoted_basedir
    pidfile = os.path.join(basedir, u"twistd.pid")
    if not os.path.exists(pidfile):
        print >>err, "%s does not look like a running node directory (no twistd.pid)" % quoted_basedir
        # we define rc=2 to mean "nothing is running, but it wasn't me who
        # stopped it"
        return COULD_NOT_STOP
    with open(pidfile, "r") as f:
        pid = f.read()

    try:
        pid = int(pid)
    except ValueError:
        # The error message below mimics a Twisted error message, which is
        # displayed when starting a node with an invalid pidfile.
        print >>err, "Pidfile %s contains non-numeric value" % pidfile
        # we define rc=2 to mean "nothing is running, but it wasn't me who
        # stopped it"
        return 2

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
            return COULD_NOT_STOP
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
