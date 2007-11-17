#! /usr/bin/python

import sys
import foolscap
from foolscap.schema import DictOf, Any
from twisted.internet import reactor
from zope.interface import implements
from twisted.python import log
#log.startLogging(sys.stderr)


class RILogObserver(foolscap.RemoteInterface):
    def msg(logmsg=DictOf(str, Any())):
        return None

class LogFetcher(foolscap.Referenceable):
    implements(RILogObserver)
    def start(self, target_furl):
        print "Connecting.."
        self._tub = foolscap.Tub()
        self._tub.startService()
        d = self._tub.getReference(target_furl)
        d.addCallback(self._got_logpublisher)
        d.addErrback(self._error)

    def _error(self, f):
        print "ERROR", f
        reactor.stop()

    def _got_logpublisher(self, publisher):
        print "Connected"
        d = publisher.callRemote("subscribe_to_all", self)
        d.addErrback(self._error)

    def remote_msg(self, d):
        print d


target_furl = sys.argv[1]
lf = LogFetcher()
lf.start(target_furl)
#print "starting.."
reactor.run()
