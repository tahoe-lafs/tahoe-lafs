#!/usr/bin/env python

import os.path, time, pickle
import foolscap
from foolscap import RemoteInterface
from foolscap.eventual import fireEventually
from foolscap.schema import DictOf, Any
from twisted.internet import reactor, defer
from zope.interface import implements
from twisted.python import usage
#from twisted.python import log
#import sys
#log.startLogging(sys.stderr)

class Options(usage.Options):
    longdesc = """
    logtool tail FURL : follow logs of the target node
    logtool gather : run as a daemon, record all logs to the current directory
    logtool dump FILE : dump the logs recorded by 'logtool gather'
    """

    def parseArgs(self, mode, *args):
        self.mode = mode
        if mode == "tail":
            target = args[0]
            if target.startswith("pb:"):
                self.target_furl = target
            elif os.path.isfile(target):
                self.target_furl = open(target, "r").read().strip()
            elif os.path.isdir(target):
                fn = os.path.join(target, "logport.furl")
                self.target_furl = open(fn, "r").read().strip()
            else:
                raise RuntimeError("Can't use tail target: %s" % target)
        elif mode == "dump":
            self.dumpfile = args[0]


class RILogObserver(RemoteInterface):
    def msg(logmsg=DictOf(str, Any())):
        return None
class RISubscription(RemoteInterface):
    pass

class RILogPublisher(RemoteInterface):
    def get_versions():
        return DictOf(str, str)
    def subscribe_to_all(observer=RILogObserver):
        return RISubscription
    def unsubscribe(subscription=Any()):
        # I don't know how to get the constraint right: unsubscribe() should
        # accept return value of subscribe_to_all()
        return None

class RILogGatherer(RemoteInterface):
    def logport(nodeid=str, logport=RILogPublisher):
        return None

class LogPrinter(foolscap.Referenceable):
    implements(RILogObserver)

    def remote_msg(self, d):
        print d

class LogTail:

    def start(self, target_furl):
        print "Connecting.."
        d = defer.maybeDeferred(self.setup_tub)
        d.addCallback(self._tub_ready, target_furl)
        return d

    def setup_tub(self):
        self._tub = foolscap.Tub()
        self._tub.startService()

    def _tub_ready(self, res, target_furl):
        d = self._tub.getReference(target_furl)
        d.addCallback(self._got_logpublisher)
        return d

    def _got_logpublisher(self, publisher):
        print "Connected"
        lp = LogPrinter()
        d = publisher.callRemote("subscribe_to_all", lp)
        return d

    def remote_msg(self, d):
        print d

class LogSaver(foolscap.Referenceable):
    implements(RILogObserver)
    def __init__(self, nodeid, savefile):
        self.nodeid = nodeid
        self.f = savefile

    def remote_msg(self, d):
        e = {"from": self.nodeid,
             "rx_time": time.time(),
             "d": d,
             }
        pickle.dump(e, self.f)

    def disconnected(self):
        del self.f
        from allmydata.util.idlib import shortnodeid_b2a
        print "LOGPORT CLOSED", shortnodeid_b2a(self.nodeid)

class LogGatherer(foolscap.Referenceable):
    implements(RILogGatherer)

    def start(self, res):
        self._savefile = open("logs.pickle", "ab", 0)
        d = self.setup_tub()
        d.addCallback(self._tub_ready)
        return d

    def setup_tub(self):
        from allmydata.util import iputil
        self._tub = foolscap.Tub(certFile="gatherer.pem")
        self._tub.startService()
        portnumfile = "portnum"
        try:
            portnum = int(open(portnumfile, "r").read())
        except (EnvironmentError, ValueError):
            portnum = 0
        self._tub.listenOn("tcp:%d" % portnum)
        d = defer.maybeDeferred(iputil.get_local_addresses_async)
        d.addCallback(self._set_location)
        return d

    def _set_location(self, local_addresses):
        l = self._tub.getListeners()[0]
        portnum = l.getPortnum()
        portnumfile = "portnum"
        open(portnumfile, "w").write("%d\n" % portnum)
        local_addresses = [ "%s:%d" % (addr, portnum,)
                            for addr in local_addresses ]
        location = ",".join(local_addresses)
        self._tub.setLocation(location)

    def _tub_ready(self, res):
        me = self._tub.registerReference(self, furlFile="log_gatherer.furl")
        print "Gatherer waiting at:", me

    def remote_logport(self, nodeid, publisher):
        from allmydata.util.idlib import shortnodeid_b2a
        short = shortnodeid_b2a(nodeid)
        print "GOT LOGPORT", short
        ls = LogSaver(nodeid, self._savefile)
        publisher.callRemote("subscribe_to_all", ls)
        publisher.notifyOnDisconnect(ls.disconnected)

class LogDumper:
    def start(self, options):
        from allmydata.util.idlib import shortnodeid_b2a
        fn = options.dumpfile
        f = open(fn, "rb")
        while True:
            try:
                e = pickle.load(f)
                short = shortnodeid_b2a(e['from'])
                when = e['rx_time']
                print "%s %r: %r" % (short, when, e['d'])
            except EOFError:
                break

class LogTool:

    def run(self, options):
        mode = options.mode
        if mode == "tail":
            lt = LogTail()
            d = fireEventually(options.target_furl)
            d.addCallback(lt.start)
            d.addErrback(self._error)
            print "starting.."
            reactor.run()
        elif mode == "gather":
            lg = LogGatherer()
            d = fireEventually()
            d.addCallback(lg.start)
            d.addErrback(self._error)
            print "starting.."
            reactor.run()
        elif mode == "dump":
            ld = LogDumper()
            ld.start(options)
        else:
            print "unknown mode '%s'" % mode
            raise NotImplementedError

    def _error(self, f):
        print "ERROR", f
        reactor.stop()

if __name__ == '__main__':
    o = Options()
    o.parseOptions()
    lt = LogTool()
    lt.run(o)
