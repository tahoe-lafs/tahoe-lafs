from __future__ import print_function

import os
import time
import signal

from allmydata.scripts.common import BasedirOptions
from allmydata.util.encodingutil import quote_local_unicode_path
from .run_common import get_pidfile, get_pid_from_pidfile

COULD_NOT_STOP = 2


class StopOptions(BasedirOptions):
    def parseArgs(self, basedir=None):
        BasedirOptions.parseArgs(self, basedir)

    def getSynopsis(self):
        return ("Usage:  %s [global-options] stop [options] [NODEDIR]"
                % (self.command_name,))


def stop(config):
    print("'tahoe stop' is deprecated; see 'tahoe run'")
    out = config.stdout
    err = config.stderr
    basedir = config['basedir']
    quoted_basedir = quote_local_unicode_path(basedir)
    print("STOPPING", quoted_basedir, file=out)
    pidfile = get_pidfile(basedir)
    pid = get_pid_from_pidfile(pidfile)
    if pid is None:
        print("%s does not look like a running node directory (no twistd.pid)" % quoted_basedir, file=err)
        # we define rc=2 to mean "nothing is running, but it wasn't me who
        # stopped it"
        return COULD_NOT_STOP
    elif pid == -1:
        print("%s contains an invalid PID file" % basedir, file=err)
        # we define rc=2 to mean "nothing is running, but it wasn't me who
        # stopped it"
        return COULD_NOT_STOP

    # kill it hard (SIGKILL), delete the twistd.pid file, then wait for the
    # process itself to go away. If it hasn't gone away after 20 seconds, warn
    # the user but keep waiting until they give up.
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError as oserr:
        if oserr.errno == 3:
            print(oserr.strerror)
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
            print("process %d is dead" % pid, file=out)
            return
        wait -= 1
        if wait < 0:
            if first_time:
                print("It looks like pid %d is still running "
                             "after %d seconds" % (pid,
                                                   (time.time() - start)), file=err)
                print("I will keep watching it until you interrupt me.", file=err)
                wait = 10
                first_time = False
            else:
                print("pid %d still running after %d seconds" % \
                      (pid, (time.time() - start)), file=err)
                wait = 10
        time.sleep(1)
    # control never reaches here: no timeout
