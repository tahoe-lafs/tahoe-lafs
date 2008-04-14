
import os
import pickle
import pprint
import sys
import time
from collections import deque

from twisted.internet import reactor, defer
from twisted.application import service
from twisted.application.internet import TimerService
from zope.interface import implements
import foolscap
from foolscap.logging.gatherer import get_local_ip_for
from twisted.internet.error import ConnectionDone, ConnectionLost
from foolscap import DeadReferenceError

from allmydata.util import log
from allmydata.interfaces import RIStatsProvider, RIStatsGatherer, IStatsProducer

class LoadMonitor(service.MultiService):
    implements(IStatsProducer)

    loop_interval = 1
    num_samples = 60

    def __init__(self, provider, warn_if_delay_exceeds=1):
        service.MultiService.__init__(self)
        self.provider = provider
        self.warn_if_delay_exceeds = warn_if_delay_exceeds
        self.started = False
        self.last = None
        self.stats = deque()
        self.timer = None

    def startService(self):
        if not self.started:
            self.started = True
            self.timer = reactor.callLater(self.loop_interval, self.loop)
        service.MultiService.startService(self)

    def stopService(self):
        self.started = False
        if self.timer:
            self.timer.cancel()
            self.timer = None
        return service.MultiService.stopService(self)

    def loop(self):
        self.timer = None
        if not self.started:
            return
        now = time.time()
        if self.last is not None:
            delay = now - self.last - self.loop_interval
            if delay > self.warn_if_delay_exceeds:
                log.msg(format='excessive reactor delay (%ss)', args=(delay,),
                        level=log.UNUSUAL)
            self.stats.append(delay)
            while len(self.stats) > self.num_samples:
                self.stats.popleft()

        self.last = now
        self.timer = reactor.callLater(self.loop_interval, self.loop)

    def get_stats(self):
        if self.stats:
            avg = sum(self.stats) / len(self.stats)
            m_x = max(self.stats)
        else:
            avg = m_x = 0
        return { 'load_monitor.avg_load': avg,
                 'load_monitor.max_load': m_x, }

class StatsProvider(foolscap.Referenceable, service.MultiService):
    implements(RIStatsProvider)

    def __init__(self, node, gatherer_furl):
        service.MultiService.__init__(self)
        self.node = node
        self.gatherer_furl = gatherer_furl

        self.counters = {}
        self.stats_producers = []

        self.load_monitor = LoadMonitor(self)
        self.load_monitor.setServiceParent(self)
        self.register_producer(self.load_monitor)

    def startService(self):
        if self.node:
            d = self.node.when_tub_ready()
            def connect(junk):
                nickname = self.node.get_config('nickname')
                self.node.tub.connectTo(self.gatherer_furl, self._connected, nickname)
            d.addCallback(connect)
        service.MultiService.startService(self)

    def count(self, name, delta=1):
        val = self.counters.setdefault(name, 0)
        self.counters[name] = val + delta

    def register_producer(self, stats_producer):
        self.stats_producers.append(IStatsProducer(stats_producer))

    def get_stats(self):
        stats = {}
        for sp in self.stats_producers:
            stats.update(sp.get_stats())
        ret = { 'counters': self.counters, 'stats': stats }
        log.msg(format='get_stats() -> %(stats)s', stats=ret, level=log.NOISY)
        return ret

    def remote_get_stats(self):
        return self.get_stats()

    def _connected(self, gatherer, nickname):
        gatherer.callRemoteOnly('provide', self, nickname or '')

class StatsGatherer(foolscap.Referenceable, service.MultiService):
    implements(RIStatsGatherer)

    poll_interval = 60

    def __init__(self, tub, basedir):
        service.MultiService.__init__(self)
        self.tub = tub
        self.basedir = basedir

        self.clients = {}
        self.nicknames = {}

    def startService(self):
        # the Tub must have a location set on it by now
        service.MultiService.startService(self)
        self.timer = TimerService(self.poll_interval, self.poll)
        self.timer.setServiceParent(self)
        self.registerGatherer()

    def get_furl(self):
        return self.my_furl

    def registerGatherer(self):
        furl_file = os.path.join(self.basedir, "stats_gatherer.furl")
        self.my_furl = self.tub.registerReference(self, furlFile=furl_file)

    def get_tubid(self, rref):
        return foolscap.SturdyRef(rref.tracker.getURL()).getTubRef().getTubID()

    def remote_provide(self, provider, nickname):
        tubid = self.get_tubid(provider)
        if tubid == '<unauth>':
            print "WARNING: failed to get tubid for %s (%s)" % (provider, nickname)
            # don't add to clients to poll (polluting data) don't care about disconnect
            return
        self.clients[tubid] = provider
        self.nicknames[tubid] = nickname

    def poll(self):
        for tubid,client in self.clients.items():
            nickname = self.nicknames.get(tubid)
            d = client.callRemote('get_stats')
            d.addCallbacks(self.got_stats, self.lost_client,
                           callbackArgs=(tubid, nickname),
                           errbackArgs=(tubid,))
            d.addErrback(self.log_client_error, tubid)

    def lost_client(self, f, tubid):
        # this is called lazily, when a get_stats request fails
        del self.clients[tubid]
        del self.nicknames[tubid]
        f.trap(DeadReferenceError, ConnectionDone, ConnectionLost)

    def log_client_error(self, f, tubid):
        log.msg("StatsGatherer: error in get_stats(), peerid=%s" % tubid,
                level=log.UNUSUAL, failure=f)

    def got_stats(self, stats, tubid, nickname):
        raise NotImplementedError()

class StdOutStatsGatherer(StatsGatherer):
    verbose = True
    def remote_provide(self, provider, nickname):
        tubid = self.get_tubid(provider)
        if self.verbose:
            print 'connect "%s" [%s]' % (nickname, tubid)
            provider.notifyOnDisconnect(self.announce_lost_client, tubid)
        StatsGatherer.remote_provide(self, provider, nickname)

    def announce_lost_client(self, tubid):
        print 'disconnect "%s" [%s]:' % (self.nicknames[tubid], tubid)

    def got_stats(self, stats, tubid, nickname):
        print '"%s" [%s]:' % (nickname, tubid)
        pprint.pprint(stats)

class PickleStatsGatherer(StdOutStatsGatherer):
    # inherit from StdOutStatsGatherer for connect/disconnect notifications

    def __init__(self, tub, basedir=".", verbose=True):
        self.verbose = verbose
        StatsGatherer.__init__(self, tub, basedir)
        self.picklefile = os.path.join(basedir, "stats.pickle")

        if os.path.exists(self.picklefile):
            f = open(self.picklefile, 'rb')
            self.gathered_stats = pickle.load(f)
            f.close()
        else:
            self.gathered_stats = {}

    def got_stats(self, stats, tubid, nickname):
        s = self.gathered_stats.setdefault(tubid, {})
        s['timestamp'] = time.time()
        s['nickname'] = nickname
        s['stats'] = stats
        self.dump_pickle()

    def dump_pickle(self):
        tmp = "%s.tmp" % (self.picklefile,)
        f = open(tmp, 'wb')
        pickle.dump(self.gathered_stats, f)
        f.close()
        if os.path.exists(self.picklefile):
            os.unlink(self.picklefile)
        os.rename(tmp, self.picklefile)

class GathererApp(object):
    def __init__(self):
        d = self.setup_tub()
        d.addCallback(self._tub_ready)

    def setup_tub(self):
        self._tub = foolscap.Tub(certFile="stats_gatherer.pem")
        self._tub.setOption("logLocalFailures", True)
        self._tub.setOption("logRemoteFailures", True)
        self._tub.startService()
        portnumfile = "portnum"
        try:
            portnum = int(open(portnumfile, "r").read())
        except (EnvironmentError, ValueError):
            portnum = 0
        self._tub.listenOn("tcp:%d" % portnum)
        d = defer.maybeDeferred(get_local_ip_for)
        d.addCallback(self._set_location)
        d.addCallback(lambda res: self._tub)
        return d

    def _set_location(self, local_address):
        if local_address is None:
            local_addresses = ["127.0.0.1"]
        else:
            local_addresses = [local_address, "127.0.0.1"]
        l = self._tub.getListeners()[0]
        portnum = l.getPortnum()
        portnumfile = "portnum"
        open(portnumfile, "w").write("%d\n" % portnum)
        local_addresses = [ "%s:%d" % (addr, portnum,)
                            for addr in local_addresses ]
        assert len(local_addresses) >= 1
        location = ",".join(local_addresses)
        self._tub.setLocation(location)

    def _tub_ready(self, tub):
        sg = PickleStatsGatherer(tub, ".")
        sg.setServiceParent(tub)
        sg.verbose = True
        print '\nStatsGatherer: %s\n' % (sg.get_furl(),)

def main(argv):
    ga = GathererApp()
    reactor.run()

if __name__ == '__main__':
    main(sys.argv)
