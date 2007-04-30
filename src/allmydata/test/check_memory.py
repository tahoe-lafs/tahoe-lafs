#! /usr/bin/env python

import os, shutil

from twisted.internet import defer, reactor, protocol, error
from twisted.application import service
from allmydata import client, introducer_and_vdrive
from allmydata.scripts import runner
from foolscap.eventual import eventually, flushEventualQueue
from twisted.python import log

class SystemFramework:
    numnodes = 5

    def __init__(self, basedir):
        self.basedir = basedir = os.path.abspath(basedir)
        if not basedir.startswith(os.path.abspath(".")):
            raise AssertionError("safety issue: basedir must be a subdir")
        if os.path.exists(basedir):
            shutil.rmtree(basedir)
        os.mkdir(basedir)
        self.sparent = service.MultiService()
        self.sparent.startService()

    def run(self):
        log.startLogging(open(os.path.join(self.basedir, "log"), "w"))
        d = defer.Deferred()
        eventually(d.callback, None)
        d.addCallback(lambda res: self.start())
        d.addErrback(log.err)
        reactor.run()

    def start(self):
        print "STARTING"
        d = self.make_introducer_and_vdrive()
        def _more(res):
            self.make_nodes()
            self.start_client()
        d.addCallback(_more)
        return d

    def tearDown(self):
        os.remove(os.path.join(self.clientdir, "suicide_prevention_hotline"))
        # the client node will shut down in a few seconds
        log.msg("shutting down SystemTest services")
        d = self.sparent.stopService()
        d.addCallback(lambda res: flushEventualQueue())
        def _done(res):
            d1 = defer.Deferred()
            reactor.callLater(self.DISCONNECT_DELAY, d1.callback, None)
            return d1
        d.addCallback(_done)
        return d

    def add_service(self, s):
        s.setServiceParent(self.sparent)
        return s

    def make_introducer_and_vdrive(self):
        introducer_and_vdrive_dir = os.path.join(self.basedir, "introducer_and_vdrive")
        os.mkdir(introducer_and_vdrive_dir)
        self.introducer_and_vdrive = self.add_service(introducer_and_vdrive.IntroducerAndVdrive(basedir=introducer_and_vdrive_dir))
        d = self.introducer_and_vdrive.when_tub_ready()
        return d

    def make_nodes(self):
        q = self.introducer_and_vdrive
        self.introducer_furl = q.urls["introducer"]
        vdrive_furl = q.urls["vdrive"]
        self.nodes = []
        for i in range(self.numnodes):
            nodedir = os.path.join(self.basedir, "node%d" % i)
            os.mkdir(nodedir)
            f = open(os.path.join(nodedir, "introducer.furl"), "w")
            f.write(self.introducer_furl)
            f.close()
            f = open(os.path.join(nodedir, "vdrive.furl"), "w")
            f.write(vdrive_furl)
            f.close()
            c = self.add_service(client.Client(basedir=nodedir))
            self.nodes.append(c)
        # the peers will start running, eventually they will connect to each
        # other and the introducer_and_vdrive

    def touch_keepalive(self):
        f = open(self.keepalive_file, "w")
        f.write("If the node notices this file at startup, it will poll and\n")
        f.write("terminate as soon as the file goes away. This prevents\n")
        f.write("leaving processes around if the test harness has an\n")
        f.write("internal failure and neglects to kill off the node\n")
        f.write("itself. The contents of this file are ignored.\n")
        f.close()

    def start_client(self):
        log.msg("MAKING CLIENT")
        clientdir = self.clientdir = os.path.join(self.basedir, "client")
        config = {'basedir': clientdir}
        runner.create_client(config)
        log.msg("DONE MAKING CLIENT")
        f = open(os.path.join(clientdir, "introducer.furl"), "w")
        f.write(self.introducer_furl + "\n")
        f.close()
        self.keepalive_file = os.path.join(clientdir, "suicide_prevention_hotline")
        self.touch_keepalive()
        # now start updating the mtime.

        pp = ClientWatcher()
        cmd = ["twistd", "-y", "client.tac"]
        env = os.environ.copy()
        self.proc = reactor.spawnProcess(pp, cmd[0], cmd, env, path=clientdir)
        log.msg("CLIENT STARTED")

    def kill_client(self):
        try:
            self.proc.signalProcess("KILL")
        except error.ProcessExitedAlready:
            pass


class ClientWatcher(protocol.ProcessProtocol):
    def outReceived(self, data):
        print "OUT:", data
    def errReceived(self, data):
        print "ERR:", data


if __name__ == '__main__':
    sf = SystemFramework("_test_memory")
    sf.run()


# add a config option that looks for a keepalive file, and if it disappears,
# shut down the node.
