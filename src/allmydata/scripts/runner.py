#! /usr/bin/env python

import os, subprocess, sys, signal, time
from twisted.python import usage

from twisted.python.procutils import which

def testtwistd(loc):
    try:
        return subprocess.call(["python", loc,], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except:
        return -1
    
twistd = None
if not twistd:
    for maybetwistd in which("twistd"):
        ret = testtwistd(maybetwistd)
        if ret == 0:
            twistd = maybetwistd
            break

if not twistd:
    for maybetwistd in which("twistd.py"):
        ret = testtwistd(maybetwistd)
        if ret == 0:
            twistd = maybetwistd
            break

if not twistd:
    maybetwistd = os.path.join(sys.prefix, 'Scripts', 'twistd')
    ret = testtwistd(maybetwistd)
    if ret == 0:
        twistd = maybetwistd

if not twistd:
    maybetwistd = os.path.join(sys.prefix, 'Scripts', 'twistd.py')
    ret = testtwistd(maybetwistd)
    if ret == 0:
        twistd = maybetwistd

if not twistd:
    print "Can't find twistd (it comes with Twisted).  Aborting."
    sys.exit(1)

class BasedirMixin:
    def postOptions(self):
        if self['basedir'] is None:
            raise usage.UsageError("<basedir> parameter is required")
        self['basedir'] = os.path.abspath(os.path.expanduser(self['basedir']))

    def parseArgs(self, *args):
        if len(args) > 0:
            self['basedir'] = args[0]
        if len(args) > 1:
            raise usage.UsageError("I wasn't expecting so many arguments")

class StartOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", ".", "which directory to start the node in"],
        ]

class StopOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", ".", "which directory to stop the node in"],
        ]

class RestartOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", ".", "which directory to restart the node in"],
        ]

class CreateClientOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to create the client in"],
        ]
    optFlags = [
        ["quiet", "q", "operate silently"],
        ]

    def parseArgs(self, *args):
        if len(args) > 0:
            self['basedir'] = args[0]
        if len(args) > 1:
            raise usage.UsageError("I wasn't expecting so many arguments")

class CreateIntroducerOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to create the introducer in"],
        ]
    optFlags = [
        ["quiet", "q", "operate silently"],
        ]

client_tac = """
# -*- python -*-

from allmydata import client
from twisted.application import service

c = client.Client()

application = service.Application("allmydata_client")
c.setServiceParent(application)
"""

introducer_tac = """
# -*- python -*-

from allmydata import introducer_and_vdrive
from twisted.application import service

c = introducer_and_vdrive.IntroducerAndVdrive()

application = service.Application("allmydata_introducer")
c.setServiceParent(application)
"""

class Options(usage.Options):
    synopsis = "Usage:  allmydata <command> [command options]"

    subCommands = [
        ["create-client", None, CreateClientOptions, "Create a client node."],
        ["create-introducer", None, CreateIntroducerOptions, "Create a introducer node."],
        ["start", None, StartOptions, "Start a node (of any type)."],
        ["stop", None, StopOptions, "Stop a node."],
        ["restart", None, RestartOptions, "Restart a node."],
        ]

    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a command")

def runner(argv, run_by_human=True):
    config = Options()
    try:
        config.parseOptions(argv)
    except usage.error, e:
        if not run_by_human:
            raise
        print "%s:  %s" % (sys.argv[0], e)
        print
        c = getattr(config, 'subOptions', config)
        print str(c)
        return 1

    command = config.subCommand
    so = config.subOptions

    if command == "create-client":
        rc = create_client(so)
    elif command == "create-introducer":
        rc = create_introducer(so)
    elif command == "start":
        rc = start(so)
    elif command == "stop":
        rc = stop(so)
    elif command == "restart":
        rc = restart(so)
    rc = rc or 0
    return rc

def run():
    rc = runner(sys.argv[1:])
    sys.exit(rc)

def create_client(config):
    basedir = config['basedir']
    if os.path.exists(basedir):
        if os.listdir(basedir):
            print "The base directory already exists: %s" % basedir
            print "To avoid clobbering anything, I am going to quit now"
            print "Please use a different directory, or delete this one"
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    f = open(os.path.join(basedir, "client.tac"), "w")
    f.write(client_tac)
    f.close()
    if not config['quiet']:
        print "client created in %s" % basedir
        print " please copy introducer.furl and vdrive.furl into the directory"

def create_introducer(config):
    basedir = config['basedir']
    if os.path.exists(basedir):
        if os.listdir(basedir):
            print "The base directory already exists: %s" % basedir
            print "To avoid clobbering anything, I am going to quit now"
            print "Please use a different directory, or delete this one"
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    f = open(os.path.join(basedir, "introducer.tac"), "w")
    f.write(introducer_tac)
    f.close()
    if not config['quiet']:
        print "introducer created in %s" % basedir

def start(config):
    basedir = config['basedir']
    if os.path.exists(os.path.join(basedir, "client.tac")):
        tac = "client.tac"
        type = "client"
    elif os.path.exists(os.path.join(basedir, "introducer.tac")):
        tac = "introducer.tac"
        type = "introducer"
    else:
        print "%s does not look like a node directory" % basedir
        if not os.path.isdir(basedir):
            print " in fact, it doesn't look like a directory at all!"
        sys.exit(1)
    os.chdir(basedir)
    rc = subprocess.call(["python", twistd, "-y", tac,])
    if rc == 0:
        print "%s node probably started" % type
        return 0
    else:
        print "%s node probably not started" % type
        return 1

def stop(config):
    basedir = config['basedir']
    pidfile = os.path.join(basedir, "twistd.pid")
    if not os.path.exists(pidfile):
        print "%s does not look like a running node directory (no twistd.pid)" % basedir
        return 1
    pid = open(pidfile, "r").read()
    pid = int(pid)

    timer = 0
    os.kill(pid, signal.SIGTERM)
    time.sleep(0.1)
    while timer < 5:
        # poll once per second until twistd.pid goes away, up to 5 seconds
        try:
            os.kill(pid, 0)
        except OSError:
            print "process %d is dead" % pid
            return
        timer += 1
        time.sleep(1)
    print "never saw process go away"
    return 1

def restart(config):
    rc = stop(config)
    if rc:
        print "not restarting"
        return rc
    return start(config)
