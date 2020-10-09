from __future__ import print_function

import json
import os
import pprint
import time
from collections import deque

# Python 2 compatibility
from future.utils import PY2
if PY2:
    from future.builtins import str  # noqa: F401

from twisted.internet import reactor
from twisted.application import service
from twisted.application.internet import TimerService
from zope.interface import implementer
from foolscap.api import eventually, DeadReferenceError, Referenceable, Tub

from allmydata.util import log
from allmydata.util.encodingutil import quote_local_unicode_path
from allmydata.interfaces import RIStatsProvider, RIStatsGatherer, IStatsProducer

@implementer(IStatsProducer)
class LoadMonitor(service.MultiService):

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

@implementer(IStatsProducer)
class CPUUsageMonitor(service.MultiService):
    HISTORY_LENGTH = 15
    POLL_INTERVAL = 60

    def __init__(self):
        service.MultiService.__init__(self)
        # we don't use time.clock() here, because the constructor is run by
        # the twistd parent process (as it loads the .tac file), whereas the
        # rest of the program will be run by the child process, after twistd
        # forks. Instead, set self.initial_cpu as soon as the reactor starts
        # up.
        self.initial_cpu = 0.0 # just in case
        eventually(self._set_initial_cpu)
        self.samples = []
        # we provide 1min, 5min, and 15min moving averages
        TimerService(self.POLL_INTERVAL, self.check).setServiceParent(self)

    def _set_initial_cpu(self):
        self.initial_cpu = time.clock()

    def check(self):
        now_wall = time.time()
        now_cpu = time.clock()
        self.samples.append( (now_wall, now_cpu) )
        while len(self.samples) > self.HISTORY_LENGTH+1:
            self.samples.pop(0)

    def _average_N_minutes(self, size):
        if len(self.samples) < size+1:
            return None
        first = -size-1
        elapsed_wall = self.samples[-1][0] - self.samples[first][0]
        elapsed_cpu = self.samples[-1][1] - self.samples[first][1]
        fraction = elapsed_cpu / elapsed_wall
        return fraction

    def get_stats(self):
        s = {}
        avg = self._average_N_minutes(1)
        if avg is not None:
            s["cpu_monitor.1min_avg"] = avg
        avg = self._average_N_minutes(5)
        if avg is not None:
            s["cpu_monitor.5min_avg"] = avg
        avg = self._average_N_minutes(15)
        if avg is not None:
            s["cpu_monitor.15min_avg"] = avg
        now_cpu = time.clock()
        s["cpu_monitor.total"] = now_cpu - self.initial_cpu
        return s


@implementer(RIStatsProvider)
class StatsProvider(Referenceable, service.MultiService):

    def __init__(self, node, gatherer_furl):
        service.MultiService.__init__(self)
        self.node = node
        self.gatherer_furl = gatherer_furl # might be None

        self.counters = {}
        self.stats_producers = []

        # only run the LoadMonitor (which submits a timer every second) if
        # there is a gatherer who is going to be paying attention. Our stats
        # are visible through HTTP even without a gatherer, so run the rest
        # of the stats (including the once-per-minute CPUUsageMonitor)
        if gatherer_furl:
            self.load_monitor = LoadMonitor(self)
            self.load_monitor.setServiceParent(self)
            self.register_producer(self.load_monitor)

        self.cpu_monitor = CPUUsageMonitor()
        self.cpu_monitor.setServiceParent(self)
        self.register_producer(self.cpu_monitor)

    def startService(self):
        if self.node and self.gatherer_furl:
            nickname_utf8 = self.node.nickname.encode("utf-8")
            self.node.tub.connectTo(self.gatherer_furl,
                                    self._connected, nickname_utf8)
        service.MultiService.startService(self)

    def count(self, name, delta=1):
        if isinstance(name, str):
            name = name.encode("utf-8")
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
        # The remote API expects keys to be bytes:
        def to_bytes(d):
            result = {}
            for (k, v) in d.items():
                if isinstance(k, str):
                    k = k.encode("utf-8")
                result[k] = v
            return result

        stats = self.get_stats()
        return {b"counters": to_bytes(stats["counters"]),
                b"stats": to_bytes(stats["stats"])}

    def _connected(self, gatherer, nickname):
        gatherer.callRemoteOnly('provide', self, nickname or '')


@implementer(RIStatsGatherer)
class StatsGatherer(Referenceable, service.MultiService):

    poll_interval = 60

    def __init__(self, basedir):
        service.MultiService.__init__(self)
        self.basedir = basedir

        self.clients = {}
        self.nicknames = {}

        self.timer = TimerService(self.poll_interval, self.poll)
        self.timer.setServiceParent(self)

    def get_tubid(self, rref):
        return rref.getRemoteTubID()

    def remote_provide(self, provider, nickname):
        tubid = self.get_tubid(provider)
        if tubid == '<unauth>':
            print("WARNING: failed to get tubid for %s (%s)" % (provider, nickname))
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
        f.trap(DeadReferenceError)

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
            print('connect "%s" [%s]' % (nickname, tubid))
            provider.notifyOnDisconnect(self.announce_lost_client, tubid)
        StatsGatherer.remote_provide(self, provider, nickname)

    def announce_lost_client(self, tubid):
        print('disconnect "%s" [%s]' % (self.nicknames[tubid], tubid))

    def got_stats(self, stats, tubid, nickname):
        print('"%s" [%s]:' % (nickname, tubid))
        pprint.pprint(stats)

class JSONStatsGatherer(StdOutStatsGatherer):
    # inherit from StdOutStatsGatherer for connect/disconnect notifications

    def __init__(self, basedir=u".", verbose=True):
        self.verbose = verbose
        StatsGatherer.__init__(self, basedir)
        self.jsonfile = os.path.join(basedir, "stats.json")

        if os.path.exists(self.jsonfile):
            try:
                with open(self.jsonfile, 'rb') as f:
                    self.gathered_stats = json.load(f)
            except Exception:
                print("Error while attempting to load stats file %s.\n"
                      "You may need to restore this file from a backup,"
                      " or delete it if no backup is available.\n" %
                      quote_local_unicode_path(self.jsonfile))
                raise
        else:
            self.gathered_stats = {}

    def got_stats(self, stats, tubid, nickname):
        s = self.gathered_stats.setdefault(tubid, {})
        s['timestamp'] = time.time()
        s['nickname'] = nickname
        s['stats'] = stats
        self.dump_json()

    def dump_json(self):
        tmp = "%s.tmp" % (self.jsonfile,)
        with open(tmp, 'wb') as f:
            json.dump(self.gathered_stats, f)
        if os.path.exists(self.jsonfile):
            os.unlink(self.jsonfile)
        os.rename(tmp, self.jsonfile)

class StatsGathererService(service.MultiService):
    furl_file = "stats_gatherer.furl"

    def __init__(self, basedir=".", verbose=False):
        service.MultiService.__init__(self)
        self.basedir = basedir
        self.tub = Tub(certFile=os.path.join(self.basedir,
                                             "stats_gatherer.pem"))
        self.tub.setServiceParent(self)
        self.tub.setOption("logLocalFailures", True)
        self.tub.setOption("logRemoteFailures", True)
        self.tub.setOption("expose-remote-exception-types", False)

        self.stats_gatherer = JSONStatsGatherer(self.basedir, verbose)
        self.stats_gatherer.setServiceParent(self)

        try:
            with open(os.path.join(self.basedir, "location")) as f:
                location = f.read().strip()
        except EnvironmentError:
            raise ValueError("Unable to find 'location' in BASEDIR, please rebuild your stats-gatherer")
        try:
            with open(os.path.join(self.basedir, "port")) as f:
                port = f.read().strip()
        except EnvironmentError:
            raise ValueError("Unable to find 'port' in BASEDIR, please rebuild your stats-gatherer")

        self.tub.listenOn(port)
        self.tub.setLocation(location)
        ff = os.path.join(self.basedir, self.furl_file)
        self.gatherer_furl = self.tub.registerReference(self.stats_gatherer,
                                                        furlFile=ff)
