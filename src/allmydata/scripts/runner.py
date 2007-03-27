#! /usr/bin/python

import os, sys, signal, time
from twisted.python import usage

class StartOptions(usage.Options):
    optParameters = [
        ["basedir", "C", ".", "which directory to start the node in"],
        ]

class StopOptions(usage.Options):
    optParameters = [
        ["basedir", "C", ".", "which directory to stop the node in"],
        ]

class RestartOptions(usage.Options):
    optParameters = [
        ["basedir", "C", ".", "which directory to restart the node in"],
        ]

class CreateClientOptions(usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to create the client in"],
        ]

    def parseArgs(self, *args):
        if len(args) > 0:
            self['basedir'] = args[0]
        if len(args) > 1:
            raise usage.UsageError("I wasn't expecting so many arguments")

    def postOptions(self):
        if self['basedir'] is None:
            raise usage.UsageError("<basedir> parameter is required")
        self['basedir'] = os.path.abspath(self['basedir'])

class CreateQueenOptions(usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to create the queen in"],
        ]

    def parseArgs(self, *args):
        if len(args) > 0:
            self['basedir'] = args[0]
        if len(args) > 1:
            raise usage.UsageError("I wasn't expecting so many arguments")

    def postOptions(self):
        if self['basedir'] is None:
            raise usage.UsageError("<basedir> parameter is required")
        self['basedir'] = os.path.abspath(self['basedir'])

client_tac = """
# -*- python -*-

from allmydata import client
from twisted.application import service

c = client.Client()

application = service.Application("allmydata_client")
c.setServiceParent(application)
"""

queen_tac = """
# -*- python -*-

from allmydata import queen
from twisted.application import service

c = queen.Queen()

application = service.Application("allmydata_queen")
c.setServiceParent(application)
"""

class Options(usage.Options):
    synopsis = "Usage:  allmydata <command> [command options]"

    subCommands = [
        ["create-client", None, CreateClientOptions, "Create a client node."],
        ["create-queen", None, CreateQueenOptions, "Create a queen node."],
        ["start", None, StartOptions, "Start a node (of any type)."],
        ["stop", None, StopOptions, "Stop a node."],
        ["restart", None, RestartOptions, "Restart a node."],
        ]

    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a command")

def run():
    config = Options()
    try:
        config.parseOptions()
    except usage.error, e:
        print "%s:  %s" % (sys.argv[0], e)
        print
        c = getattr(config, 'subOptions', config)
        print str(c)
        sys.exit(1)

    command = config.subCommand
    so = config.subOptions

    if command == "create-client":
        rc = create_client(so)
    elif command == "create-queen":
        rc = create_queen(so)
    elif command == "start":
        rc = start(so)
    elif command == "stop":
        rc = stop(so)
    elif command == "restart":
        rc = restart(so)
    rc = rc or 0
    sys.exit(rc)

def create_client(config):
    basedir = config['basedir']
    os.mkdir(basedir)
    f = open(os.path.join(basedir, "client.tac"), "w")
    f.write(client_tac)
    f.close()
    print "client created in %s" % basedir
    print " please copy introducer.furl and vdrive.furl into the directory"

def create_queen(config):
    basedir = config['basedir']
    os.mkdir(basedir)
    f = open(os.path.join(basedir, "queen.tac"), "w")
    f.write(queen_tac)
    f.close()
    print "queen created in %s" % basedir

def start(config):
    basedir = config['basedir']
    if os.path.exists(os.path.join(basedir, "client.tac")):
        tac = "client.tac"
        type = "client"
    elif os.path.exists(os.path.join(basedir, "queen.tac")):
        tac = "queen.tac"
        type = "queen"
    else:
        print "%s does not look like a node directory" % basedir
        sys.exit(1)
    os.chdir(basedir)
    rc = os.system("twistd -y %s" % tac)
    if rc == 0:
        print "node probably started"
        return 0
    else:
        print "node probably not started"
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
