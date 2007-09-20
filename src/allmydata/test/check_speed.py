#! /bin/env/python

import os, sys
from twisted.internet import reactor, defer
from twisted.python import log
from twisted.application import service
from foolscap import Tub, eventual

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

    def do_test(self):
        print "doing test"
        rr = self.client_rref
        d = rr.callRemote("get_memory_usage")
        def _got(res):
            print "MEMORY USAGE:", res
        d.addCallback(_got)
        d.addCallback(lambda res: rr.callRemote("upload_speed_test", 1000))
        d.addCallback(self.record_time, "startup")
        d.addCallback(lambda res: rr.callRemote("upload_speed_test", int(1e6)))
        d.addCallback(self.record_time, "1MB.1")
        d.addCallback(lambda res: rr.callRemote("upload_speed_test", int(1e6)))
        d.addCallback(self.record_time, "1MB.2")
        return d

    def tearDown(self, res):
        d = self.base_service.stopService()
        d.addCallback(lambda ignored: res)
        return d


if __name__ == '__main__':
    test_client_dir = sys.argv[1]
    st = SpeedTest(test_client_dir)
    st.run()
