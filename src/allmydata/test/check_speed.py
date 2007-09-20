#! /bin/env/python

import os, sys
from twisted.internet import reactor, defer
from twisted.python import log
from twisted.application import service
from foolscap import Tub, eventual

MB = 1000000

class SpeedTest:
    def __init__(self, test_client_dir):
        #self.real_stderr = sys.stderr
        log.startLogging(open("st.log", "a"), setStdout=False)
        f = open(os.path.join(test_client_dir, "control.furl"), "r")
        self.control_furl = f.read().strip()
        f.close()
        self.base_service = service.MultiService()
        self.failed = None
        self.times = {}

    def run(self):
        print "STARTING"
        d = eventual.fireEventually()
        d.addCallback(lambda res: self.setUp())
        d.addCallback(lambda res: self.do_test())
        d.addBoth(self.tearDown)
        def _err(err):
            self.failed = err
            log.err(err)
            print err
        d.addErrback(_err)
        def _done(res):
            reactor.stop()
            return res
        d.addBoth(_done)
        reactor.run()
        if self.failed:
            print "EXCEPTION"
            print self.failed
            sys.exit(1)

    def setUp(self):
        self.base_service.startService()
        self.tub = Tub()
        self.tub.setServiceParent(self.base_service)
        d = self.tub.getReference(self.control_furl)
        def _gotref(rref):
            self.client_rref = rref
            print "Got Client Control reference"
            return self.stall(5)
        d.addCallback(_gotref)
        return d

    def stall(self, delay, result=None):
        d = defer.Deferred()
        reactor.callLater(delay, d.callback, result)
        return d

    def record_time(self, time, key):
        print "TIME (%s): %s" % (key, time)
        self.times[key] = time

    def one_test(self, res, name, count, size):
        d = self.client_rref.callRemote("upload_speed_test", count, size)
        d.addCallback(self.record_time, name)
        return d

    def do_test(self):
        print "doing test"
        rr = self.client_rref
        d = rr.callRemote("get_memory_usage")
        def _got(res):
            print "MEMORY USAGE:", res
        d.addCallback(_got)
        d.addCallback(self.one_test, "startup", 1, 1000) # ignore this one
        d.addCallback(self.one_test, "1x 200B", 1, 200)
        d.addCallback(self.one_test, "10x 200B", 10, 200)
        #d.addCallback(self.one_test, "100x 200B", 100, 200)
        d.addCallback(self.one_test, "1MB", 1, 1*MB)
        d.addCallback(self.one_test, "10MB", 1, 10*MB)
        d.addCallback(self.calculate_speed)
        return d

    def calculate_speed(self, res):
        perfile = self.times["1x 200B"]
        # time = A*size+B
        # we assume that A*200bytes is negligible
        B = self.times["10x 200B"] / 10
        print "per-file time: %.3fs" % B
        A1 = 1*MB / (self.times["1MB"] - B) # in bytes per second
        print "speed (1MB):", self.number(A1, "Bps")
        A2 = 10*MB / (self.times["10MB"] - B)
        print "speed (10MB):", self.number(A2, "Bps")

    def number(self, value, suffix=""):
        scaling = 1
        if value < 1:
            fmt = "%1.2g%s"
        elif value < 100:
            fmt = "%.1f%s"
        elif value < 1000:
            fmt = "%d%s"
        elif value < 1e6:
            fmt = "%.2fk%s"; scaling = 1e3
        elif value < 1e9:
            fmt = "%.2fM%s"; scaling = 1e6
        elif value < 1e12:
            fmt = "%.2fG%s"; scaling = 1e9
        elif value < 1e15:
            fmt = "%.2fT%s"; scaling = 1e12
        elif value < 1e18:
            fmt = "%.2fP%s"; scaling = 1e15
        else:
            fmt = "huge! %g%s"
        return fmt % (value / scaling, suffix)

    def tearDown(self, res):
        d = self.base_service.stopService()
        d.addCallback(lambda ignored: res)
        return d


if __name__ == '__main__':
    test_client_dir = sys.argv[1]
    st = SpeedTest(test_client_dir)
    st.run()
