#! /usr/bin/env python

import os, shutil

from twisted.internet import defer, reactor, protocol, error
from twisted.application import service, internet
from allmydata import client, introducer_and_vdrive
from allmydata.scripts import runner
from allmydata.util import testutil
import foolscap
from foolscap import eventual
from twisted.python import log

class SystemFramework(testutil.PollMixin):
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
        self.proc = None
        self.tub = foolscap.Tub()
        self.tub.setServiceParent(self.sparent)

    def run(self):
        log.startLogging(open(os.path.join(self.basedir, "log"), "w"),
                         setStdout=False)
        #logfile = open(os.path.join(self.basedir, "log"), "w")
        #flo = log.FileLogObserver(logfile)
        #log.startLoggingWithObserver(flo.emit, setStdout=False)
        d = eventual.fireEventually()
        d.addCallback(lambda res: self.setUp())
        d.addCallback(lambda res: self.do_test())
        d.addBoth(self.tearDown)
        def _err(err):
            log.err(err)
            print err
        d.addErrback(_err)
        d.addBoth(lambda res: reactor.stop())
        reactor.run()

    def setUp(self):
        print "STARTING"
        d = self.make_introducer_and_vdrive()
        def _more(res):
            self.make_nodes()
            return self.start_client()
        d.addCallback(_more)
        def _record_control_furl(control_furl):
            self.control_furl = control_furl
            print "OBTAINING '%s'" % (control_furl,)
            return self.tub.getReference(self.control_furl)
        d.addCallback(_record_control_furl)
        def _record_control(control_rref):
            self.control_rref = control_rref
            return control_rref.callRemote("wait_for_client_connections",
                                           self.numnodes+1)
        d.addCallback(_record_control)
        def _ready(res):
            print "CLIENT READY"
        d.addCallback(_ready)
        return d

    def tearDown(self, passthrough):
        # the client node will shut down in a few seconds
        #os.remove(os.path.join(self.clientdir, "suicide_prevention_hotline"))
        log.msg("shutting down SystemTest services")
        d = defer.succeed(None)
        if self.proc:
            d.addCallback(lambda res: self.kill_client())
        d.addCallback(lambda res: self.sparent.stopService())
        d.addCallback(lambda res: eventual.flushEventualQueue())
        d.addCallback(lambda res: passthrough)
        return d

    def add_service(self, s):
        s.setServiceParent(self.sparent)
        return s

    def make_introducer_and_vdrive(self):
        iv_basedir = os.path.join(self.basedir, "introducer_and_vdrive")
        os.mkdir(iv_basedir)
        iv = introducer_and_vdrive.IntroducerAndVdrive(basedir=iv_basedir)
        self.introducer_and_vdrive = self.add_service(iv)
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
        f.write("""\
If the node notices this file at startup, it will poll every 5 seconds and
terminate if the file is more than 10 seconds old, or if it has been deleted.
If the test harness has an internal failure and neglects to kill off the node
itself, this helps to avoid leaving processes lying around. The contents of
this file are ignored.
        """)
        f.close()

    def start_client(self):
        # this returns a Deferred that fires with the client's control.furl
        log.msg("MAKING CLIENT")
        clientdir = self.clientdir = os.path.join(self.basedir, "client")
        config = {'basedir': clientdir, 'quiet': True}
        runner.create_client(config)
        log.msg("DONE MAKING CLIENT")
        f = open(os.path.join(clientdir, "introducer.furl"), "w")
        f.write(self.introducer_furl + "\n")
        f.close()
        f = open(os.path.join(clientdir, "vdrive.furl"), "w")
        f.write(self.introducer_furl + "\n")
        f.close()
        self.keepalive_file = os.path.join(clientdir,
                                           "suicide_prevention_hotline")
        # now start updating the mtime.
        self.touch_keepalive()
        ts = internet.TimerService(4.0, self.touch_keepalive)
        ts.setServiceParent(self.sparent)

        pp = ClientWatcher()
        self.proc_done = pp.d = defer.Deferred()
        cmd = ["twistd", "-y", "client.tac"]
        env = os.environ.copy()
        self.proc = reactor.spawnProcess(pp, cmd[0], cmd, env, path=clientdir)
        log.msg("CLIENT STARTED")

        # now we wait for the client to get started. we're looking for the
        # control.furl file to appear.
        furl_file = os.path.join(clientdir, "control.furl")
        def _check():
            return os.path.exists(furl_file)
        d = self.poll(_check, 0.1)
        # once it exists, wait a moment before we read from it, just in case
        # it hasn't finished writing the whole thing. Ideally control.furl
        # would be created in some atomic fashion, or made non-readable until
        # it's ready, but I can't think of an easy way to do that, and I
        # think the chances that we'll observe a half-write are pretty low.
        def _stall(res):
            d2 = defer.Deferred()
            reactor.callLater(0.1, d2.callback, None)
            return d2
        d.addCallback(_stall)
        def _read(res):
            f = open(furl_file, "r")
            furl = f.read()
            return furl.strip()
        d.addCallback(_read)
        return d


    def kill_client(self):
        # returns a Deferred that fires when the process exits. This may only
        # be called once.
        try:
            self.proc.signalProcess("KILL")
        except error.ProcessExitedAlready:
            pass
        return self.proc_done


    def create_data(self, name, size):
        filename = os.path.join(self.basedir, name + ".data")
        f = open(filename, "wb")
        block = "a" * 8192
        while size > 0:
            l = min(size, 8192)
            f.write(block[:l])
            size -= l
        return filename

    def do_test(self):
        print "CLIENT STARTED"
        print "FURL", self.control_furl
        print "RREF", self.control_rref
        print
        kB = 1000; MB = 1000*1000
        files = {}
        uris = {}
        control = self.control_rref

        def _print_usage(res=None):
            d = control.callRemote("get_memory_usage")
            def _print(stats):
                print "VmSize: %9d  VmPeak: %9d" % (stats["VmSize"],
                                                    stats["VmPeak"])
            d.addCallback(_print)
            return d

        def _do_upload(res, size):
            name = '%d' % size
            files[name] = self.create_data(name, size)
            d = control.callRemote("upload_from_file_to_uri", files[name])
            def _done(uri):
                uris[name] = uri
                print "uploaded %s" % name
            d.addCallback(_done)
            return d

        d = _print_usage()

        for i in range(10):
            d.addCallback(_do_upload, size=10*kB+i)
            d.addCallback(_print_usage)

        for i in range(10):
            d.addCallback(_do_upload, size=10*MB+i)
            d.addCallback(_print_usage)

        #d.addCallback(self.stall)
        def _done(res):
            print "FINISHING"
        d.addCallback(_done)
        return d

    def stall(self, res):
        d = defer.Deferred()
        reactor.callLater(5, d.callback, None)
        return d


class ClientWatcher(protocol.ProcessProtocol):
    def outReceived(self, data):
        print "OUT:", data
    def errReceived(self, data):
        print "ERR:", data
    def processEnded(self, reason):
        self.d.callback(None)


if __name__ == '__main__':
    sf = SystemFramework("_test_memory")
    sf.run()

