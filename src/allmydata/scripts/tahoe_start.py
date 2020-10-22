from __future__ import print_function

import os
import io
import sys
import time
import subprocess
from os.path import join, exists

from allmydata.scripts.common import BasedirOptions
from allmydata.scripts.default_nodedir import _default_nodedir
from allmydata.util.encodingutil import quote_local_unicode_path

from .run_common import MyTwistdConfig, identify_node_type


class StartOptions(BasedirOptions):
    subcommand_name = "start"
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


def start(config):
    """
    Start a tahoe node (daemonize it and confirm startup)

    We run 'tahoe daemonize' with all the options given to 'tahoe
    start' and then watch the log files for the correct text to appear
    (e.g. "introducer started"). If that doesn't happen within a few
    seconds, an error is printed along with all collected logs.
    """
    print("'tahoe start' is deprecated; see 'tahoe run'")
    out = config.stdout
    err = config.stderr
    basedir = config['basedir']
    quoted_basedir = quote_local_unicode_path(basedir)
    print("STARTING", quoted_basedir, file=out)
    if not os.path.isdir(basedir):
        print("%s does not look like a directory at all" % quoted_basedir, file=err)
        return 1
    nodetype = identify_node_type(basedir)
    if not nodetype:
        print("%s is not a recognizable node directory" % quoted_basedir, file=err)
        return 1

    # "tahoe start" attempts to monitor the logs for successful
    # startup -- but we can't always do that.

    can_monitor_logs = False
    if (nodetype in (u"client", u"introducer")
        and "--nodaemon" not in config.twistd_args
        and "--syslog" not in config.twistd_args
        and "--logfile" not in config.twistd_args):
        can_monitor_logs = True

    if "--help" in config.twistd_args:
        return 0

    if not can_monitor_logs:
        print("Custom logging options; can't monitor logs for proper startup messages", file=out)
        return 1

    # before we spawn tahoe, we check if "the log file" exists or not,
    # and if so remember how big it is -- essentially, we're doing
    # "tail -f" to see what "this" incarnation of "tahoe daemonize"
    # spews forth.
    starting_offset = 0
    log_fname = join(basedir, 'logs', 'twistd.log')
    if exists(log_fname):
        with open(log_fname, 'r') as f:
            f.seek(0, 2)
            starting_offset = f.tell()

    # spawn tahoe. Note that since this daemonizes, it should return
    # "pretty fast" and with a zero return-code, or else something
    # Very Bad has happened.
    try:
        args = [sys.executable] if not getattr(sys, 'frozen', False) else []
        for i, arg in enumerate(sys.argv):
            if arg in ['start', 'restart']:
                args.append('daemonize')
            else:
                args.append(arg)
        subprocess.check_call(args)
    except subprocess.CalledProcessError as e:
        return e.returncode

    # now, we have to determine if tahoe has actually started up
    # successfully or not. so, we start sucking up log files and
    # looking for "the magic string", which depends on the node type.

    magic_string = u'{} running'.format(nodetype)
    with io.open(log_fname, 'r') as f:
        f.seek(starting_offset)

        collected = u''
        overall_start = time.time()
        while time.time() - overall_start < 60:
            this_start = time.time()
            while time.time() - this_start < 5:
                collected += f.read()
                if magic_string in collected:
                    if not config.parent['quiet']:
                        print("Node has started successfully", file=out)
                    return 0
                if 'Traceback ' in collected:
                    print("Error starting node; see '{}' for more:\n\n{}".format(
                        log_fname,
                        collected,
                    ), file=err)
                    return 1
                time.sleep(0.1)
            print("Still waiting up to {}s for node startup".format(
                60 - int(time.time() - overall_start)
            ), file=out)

        print("Something has gone wrong starting the node.", file=out)
        print("Logs are available in '{}'".format(log_fname), file=out)
        print("Collected for this run:", file=out)
        print(collected, file=out)
        return 1
