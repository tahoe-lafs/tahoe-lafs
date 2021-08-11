"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
    from time import clock as process_time
else:
    from time import process_time
import time

from twisted.application import service
from twisted.application.internet import TimerService
from zope.interface import implementer
from foolscap.api import eventually

from allmydata.util import log, dictutil
from allmydata.interfaces import IStatsProducer

@implementer(IStatsProducer)
class CPUUsageMonitor(service.MultiService):
    HISTORY_LENGTH = 15
    POLL_INTERVAL = 60  # type: float

    def __init__(self):
        service.MultiService.__init__(self)
        # we don't use process_time() here, because the constructor is run by
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
        self.initial_cpu = process_time()

    def check(self):
        now_wall = time.time()
        now_cpu = process_time()
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
        now_cpu = process_time()
        s["cpu_monitor.total"] = now_cpu - self.initial_cpu
        return s


class StatsProvider(service.MultiService):

    def __init__(self, node):
        service.MultiService.__init__(self)
        self.node = node

        self.counters = dictutil.UnicodeKeyDict()
        self.stats_producers = []
        self.cpu_monitor = CPUUsageMonitor()
        self.cpu_monitor.setServiceParent(self)
        self.register_producer(self.cpu_monitor)

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
