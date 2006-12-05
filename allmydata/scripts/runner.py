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
        ["basedir", "C", ".", "which directory to create the client in"],
        ]
class CreateQueenOptions(usage.Options):
    optParameters = [
        ["basedir", "C", ".", "which directory to create the queen in"],
        ]

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

    subcommands = [
        ["create-client", None, CreateClientOptions],
        ["create-queen", None, CreateQueenOptions],
        ["start", None, StartOptions],
        ["stop", None, StopOptions],
        ["restart", None, RestartOptions],
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
    print "client created, please copy roster_pburl into the directory"

def create_queen(config):
    basedir = config['basedir']
    os.mkdir(basedir)
    f = open(os.path.join(basedir, "queen.tac"), "w")
    f.write(queen_tac)
    f.close()
    print "queen created"

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
    os.kill(pid, signal.TERM)
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
