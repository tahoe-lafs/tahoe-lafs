# -*- python -*-

"""
# run this tool on a linux box in its own directory, with a file named
# 'pids.txt' describing which processes to watch. It will follow CPU usage of
# the given processes, and compute 1/5/15-minute moving averages for each
# process. These averages can be retrieved from a foolscap connection
# (published at ./watcher.furl), or through an HTTP query (using ./webport).

# Each line of pids.txt describes a single process. Blank lines and ones that
# begin with '#' are ignored. Each line is either "PID" or "PID NAME" (space
# separated). PID is either a numeric process ID, a pathname to a file that
# contains a process ID, or a pathname to a directory that contains a
# twistd.pid file (which contains a process ID). NAME is an arbitrary string
# that will be used to describe the process to watcher.furl subscribers, and
# defaults to PID if not provided.
"""

# TODO:
#  built-in graphs on web interface



import pickle, os.path, time, pprint
from twisted.application import internet, service, strports
from twisted.web import server, resource, http
from twisted.python import log
import simplejson
from foolscap import Tub, Referenceable, RemoteInterface, eventual
from foolscap.schema import ListOf, TupleOf
from zope.interface import implements

def read_cpu_times(pid):
    data = open("/proc/%d/stat" % pid, "r").read()
    data = data.split()
    times = data[13:17]
    # the values in /proc/%d/stat are in ticks, I think. My system has
    # CONFIG_HZ_1000=y in /proc/config.gz but nevertheless the numbers in
    # 'stat' appear to be 10ms each.
    HZ = 100
    userspace_seconds = int(times[0]) * 1.0 / HZ
    system_seconds = int(times[1]) * 1.0 / HZ
    child_userspace_seconds = int(times[2]) * 1.0 / HZ
    child_system_seconds = int(times[3]) * 1.0 / HZ
    return (userspace_seconds, system_seconds)


def read_pids_txt():
    processes = []
    for line in open("pids.txt", "r").readlines():
        line = line.strip()
        if not line or line[0] == "#":
            continue
        parts = line.split()
        pidthing = parts[0]
        if len(parts) > 1:
            name = parts[1]
        else:
            name = pidthing
        pid = None
        try:
            pid = int(pidthing)
        except ValueError:
            pidfile = os.path.expanduser(pidthing)
            if os.path.isdir(pidfile):
                pidfile = os.path.join(pidfile, "twistd.pid")
            try:
                pid = int(open(pidfile, "r").read().strip())
            except EnvironmentError:
                pass
        if pid is not None:
            processes.append( (pid, name) )
    return processes

Averages = ListOf( TupleOf(str, float, float, float) )
class RICPUWatcherSubscriber(RemoteInterface):
    def averages(averages=Averages):
        return None

class RICPUWatcher(RemoteInterface):
    def get_averages():
        """Return a list of rows, one for each process I am watching. Each
        row is (name, 1-min-avg, 5-min-avg, 15-min-avg), where 'name' is a
        string, and the averages are floats from 0.0 to 1.0 . Each average is
        the percentage of the CPU that this process has used: the change in
        CPU time divided by the change in wallclock time.
        """
        return Averages

    def subscribe(observer=RICPUWatcherSubscriber):
        """Arrange for the given observer to get an 'averages' message every
        time the averages are updated. This message will contain a single
        argument, the same list of tuples that get_averages() returns."""
        return None

class CPUWatcher(service.MultiService, resource.Resource, Referenceable):
    implements(RICPUWatcher)
    POLL_INTERVAL = 30 # seconds
    HISTORY_LIMIT = 15 * 60 # 15min
    AVERAGES = (1*60, 5*60, 15*60) # 1min, 5min, 15min

    def __init__(self):
        service.MultiService.__init__(self)
        resource.Resource.__init__(self)
        try:
            self.history = pickle.load(open("history.pickle", "rb"))
        except EnvironmentError:
            self.history = {}
        self.current = []
        self.observers = set()
        ts = internet.TimerService(self.POLL_INTERVAL, self.poll)
        ts.setServiceParent(self)

    def startService(self):
        service.MultiService.startService(self)

        try:
            desired_webport = open("webport", "r").read().strip()
        except EnvironmentError:
            desired_webport = None
        webport = desired_webport or "tcp:0"
        root = self
        serv = strports.service(webport, server.Site(root))
        serv.setServiceParent(self)
        if not desired_webport:
            got_port = serv._port.getHost().port
            open("webport", "w").write("tcp:%d\n" % got_port)

        self.tub = Tub(certFile="watcher.pem")
        self.tub.setServiceParent(self)
        try:
            desired_tubport = open("tubport", "r").read().strip()
        except EnvironmentError:
            desired_tubport = None
        tubport = desired_tubport or "tcp:0"
        l = self.tub.listenOn(tubport)
        if not desired_tubport:
            got_port = l.getPortnum()
            open("tubport", "w").write("tcp:%d\n" % got_port)
        d = self.tub.setLocationAutomatically()
        d.addCallback(self._tub_ready)
        d.addErrback(log.err)

    def _tub_ready(self, res):
        self.tub.registerReference(self, furlFile="watcher.furl")


    def getChild(self, path, req):
        if path == "":
            return self
        return resource.Resource.getChild(self, path, req)

    def render(self, req):
        t = req.args.get("t", ["html"])[0]
        ctype = "text/plain"
        data = ""
        if t == "html":
            data = "# name, 1min, 5min, 15min\n"
            data += pprint.pformat(self.current) + "\n"
        elif t == "json":
            #data = str(self.current) + "\n" # isn't that convenient? almost.
            data = simplejson.dumps(self.current, indent=True)
        else:
            req.setResponseCode(http.BAD_REQUEST)
            data = "Unknown t= %s\n" % t
        req.setHeader("content-type", ctype)
        return data

    def remote_get_averages(self):
        return self.current
    def remote_subscribe(self, observer):
        self.observers.add(observer)

    def notify(self, observer):
        d = observer.callRemote("averages", self.current)
        def _error(f):
            log.msg("observer error, removing them")
            log.msg(f)
            self.observers.discard(observer)
        d.addErrback(_error)

    def poll(self):
        max_history = self.HISTORY_LIMIT / self.POLL_INTERVAL
        current = []
        try:
            processes = read_pids_txt()
        except:
            log.err()
            return
        for (pid, name) in processes:
            if pid not in self.history:
                self.history[pid] = []
            now = time.time()
            try:
                (user_seconds, sys_seconds) = read_cpu_times(pid)
                self.history[pid].append( (now, user_seconds, sys_seconds) )
                while len(self.history[pid]) > max_history+1:
                    self.history[pid].pop(0)
            except:
                log.err()
        pickle.dump(self.history, open("history.pickle", "wb"))
        for (pid, name) in processes:
            row = [name]
            for avg in self.AVERAGES:
                row.append(self._average_N(pid, avg))
            current.append(tuple(row))
        self.current = current
        print current
        for ob in self.observers:
            eventual.eventually(self.notify, ob)

    def _average_N(self, pid, seconds):
        num_samples = seconds / self.POLL_INTERVAL
        samples = self.history[pid]
        if len(samples) < num_samples+1:
            return None
        first = -num_samples-1
        elapsed_wall = samples[-1][0] - samples[first][0]
        elapsed_user = samples[-1][1] - samples[first][1]
        elapsed_sys = samples[-1][2] - samples[first][2]
        if elapsed_wall == 0.0:
            return 0.0
        return (elapsed_user+elapsed_sys) / elapsed_wall

application = service.Application("cpu-watcher")
CPUWatcher().setServiceParent(application)
