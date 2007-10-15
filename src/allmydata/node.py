
import datetime, new, os.path, re
from base64 import b32decode, b32encode

import twisted
from twisted.python import log, logfile
from twisted.application import service
from twisted.internet import defer, reactor
from foolscap import Tub, eventual
from allmydata.util import iputil, observer, humanreadable, fileutil
from allmydata.util.assertutil import precondition

# Just to get their versions:
import allmydata
import zfec
import foolscap

# group 1 will be addr (dotted quad string), group 3 if any will be portnum (string)
ADDR_RE=re.compile("^([1-9][0-9]*\.[1-9][0-9]*\.[1-9][0-9]*\.[1-9][0-9]*)(:([1-9][0-9]*))?$")


def formatTimeTahoeStyle(self, when):
    # we want UTC timestamps that look like:
    #  2007-10-12 00:26:28.566Z [Client] rnp752lz: 'client running'
    d = datetime.datetime.utcfromtimestamp(when)
    if d.microsecond:
        return d.isoformat(" ")[:-3]+"Z"
    else:
        return d.isoformat(" ") + ".000Z"

class Node(service.MultiService):
    # this implements common functionality of both Client nodes, Introducer 
    # nodes, and Vdrive nodes
    NODETYPE = "unknown NODETYPE"
    PORTNUMFILE = None
    CERTFILE = "node.pem"
    LOCAL_IP_FILE = "advertised_ip_addresses"

    def __init__(self, basedir="."):
        service.MultiService.__init__(self)
        self.basedir = os.path.abspath(basedir)
        self._tub_ready_observerlist = observer.OneShotObserverList()
        certfile = os.path.join(self.basedir, self.CERTFILE)
        self.tub = Tub(certFile=certfile)
        os.chmod(certfile, 0600)
        self.tub.setOption("logLocalFailures", True)
        self.tub.setOption("logRemoteFailures", True)
        self.nodeid = b32decode(self.tub.tubID.upper()) # binary format
        self.write_config("my_nodeid", b32encode(self.nodeid).lower() + "\n")
        self.short_nodeid = b32encode(self.nodeid).lower()[:8] # ready for printing
        assert self.PORTNUMFILE, "Your node.Node subclass must provide PORTNUMFILE"
        self._portnumfile = os.path.join(self.basedir, self.PORTNUMFILE)
        try:
            portnum = int(open(self._portnumfile, "rU").read())
        except (EnvironmentError, ValueError):
            portnum = 0
        self.tub.listenOn("tcp:%d" % portnum)
        # we must wait until our service has started before we can find out
        # our IP address and thus do tub.setLocation, and we can't register
        # any services with the Tub until after that point
        self.tub.setServiceParent(self)
        self.logSource="Node"

        AUTHKEYSFILEBASE = "authorized_keys."
        for f in os.listdir(self.basedir):
            if f.startswith(AUTHKEYSFILEBASE):
                keyfile = os.path.join(self.basedir, f)
                portnum = int(f[len(AUTHKEYSFILEBASE):])
                from allmydata import manhole
                m = manhole.AuthorizedKeysManhole(portnum, keyfile)
                m.setServiceParent(self)
                self.log("AuthorizedKeysManhole listening on %d" % portnum)

        self.setup_logging()
        self.log("Node constructed.  tahoe version: %s, foolscap: %s,"
                 " twisted: %s, zfec: %s"
                 % (allmydata.__version__, foolscap.__version__,
                    twisted.__version__, zfec.__version__,))

    def get_config(self, name, mode="r", required=False):
        """Get the (string) contents of a config file, or None if the file
        did not exist. If required=True, raise an exception rather than
        returning None. Any leading or trailing whitespace will be stripped
        from the data."""
        fn = os.path.join(self.basedir, name)
        try:
            return open(fn, mode).read().strip()
        except EnvironmentError:
            if not required:
                return None
            raise

    def get_or_create_config(self, name, default_fn, mode="w", filemode=None):
        """Try to get the (string) contents of a config file, and return it.
        Any leading or trailing whitespace will be stripped from the data.

        If the file does not exist, try to create it using default_fn, and
        then return the value that was written. If 'default_fn' is a string,
        use it as a default value. If not, treat it as a 0-argument callable
        which is expected to return a string.
        """
        value = self.get_config(name)
        if value is None:
            if isinstance(default_fn, (str, unicode)):
                value = default_fn
            else:
                value = default_fn()
            fn = os.path.join(self.basedir, name)
            try:
                f = open(fn, mode)
                f.write(value)
                f.close()
                if filemode is not None:
                    os.chmod(fn, filemode)
            except EnvironmentError, e:
                self.log("Unable to write config file '%s'" % fn)
                self.log(e)
            value = value.strip()
        return value

    def write_config(self, name, value, mode="w"):
        """Write a string to a config file."""
        fn = os.path.join(self.basedir, name)
        try:
            open(fn, mode).write(value)
        except EnvironmentError, e:
            self.log("Unable to write config file '%s'" % fn)
            self.log(e)

    def get_versions(self):
        return {'allmydata': allmydata.__version__,
                'foolscap': foolscap.__version__,
                'twisted': twisted.__version__,
                'zfec': zfec.__version__,
                }

    def startService(self):
        # note: this class can only be started and stopped once.
        self.log("Node.startService")
        eventual.eventually(self._startService)

    def _startService(self):
        precondition(reactor.running)
        self.log("Node._startService")

        service.MultiService.startService(self)
        d = defer.succeed(None)
        d.addCallback(lambda res: iputil.get_local_addresses_async())
        d.addCallback(self._setup_tub)
        d.addCallback(lambda res: self.tub_ready())
        def _ready(res):
            self.log("%s running" % self.NODETYPE)
            self._tub_ready_observerlist.fire(self)
            return self
        d.addCallback(_ready)
        def _die(failure):
            self.log('_startService() failed')
            log.err(failure)
            #reactor.stop() # for unknown reasons, reactor.stop() isn't working.  [ ] TODO
            self.log('calling os.abort()')
            os.abort()
        d.addErrback(_die)

    def stopService(self):
        self.log("Node.stopService")
        d = self._tub_ready_observerlist.when_fired()
        def _really_stopService(ignored):
            self.log("Node._really_stopService")
            return service.MultiService.stopService(self)
        d.addCallback(_really_stopService)
        return d

    def shutdown(self):
        """Shut down the node. Returns a Deferred that fires (with None) when
        it finally stops kicking."""
        self.log("Node.shutdown")
        return self.stopService()

    def setup_logging(self):
        # we replace the log observer that twistd set up for us, with a form
        # that uses better timestamps. First, shut down all existing
        # file-based observers (leaving trial's error-watching observers in
        # place).
        for o in log.theLogPublisher.observers:
            # o might be a FileLogObserver's .emit method
            if type(o) is type(self.setup_logging): # bound method
                ob = o.im_self
                if isinstance(ob, log.FileLogObserver):
                    newmeth = new.instancemethod(formatTimeTahoeStyle, ob, ob.__class__)
                    ob.formatTime = newmeth
        # TODO: twisted >2.5.0 offers maxRotatedFiles=50

    def log(self, msg, src="", args=()):
        if src:
            logsrc = src
        else:
            logsrc = self.logSource
        if args:
            try:
                msg = msg % tuple(map(humanreadable.hr, args))
            except TypeError, e:
                msg = "ERROR: output string '%s' contained invalid %% expansion, error: %s, args: %s\n" % (`msg`, e, `args`)

        log.callWithContext({"system":logsrc},
                            log.msg,
                            (self.short_nodeid + ": " + humanreadable.hr(msg)))

    def _setup_tub(self, local_addresses):
        # we can't get a dynamically-assigned portnum until our Tub is
        # running, which means after startService.
        l = self.tub.getListeners()[0]
        portnum = l.getPortnum()
        # record which port we're listening on, so we can grab the same one next time
        open(self._portnumfile, "w").write("%d\n" % portnum)

        local_addresses = [ "%s:%d" % (addr, portnum,) for addr in local_addresses ]

        addresses = []
        try:
            for addrline in open(os.path.join(self.basedir, self.LOCAL_IP_FILE), "rU"):
                mo = ADDR_RE.search(addrline)
                if mo:
                    (addr, dummy, aportnum,) = mo.groups()
                    if aportnum is None:
                        aportnum = portnum
                    addresses.append("%s:%d" % (addr, int(aportnum),))
        except EnvironmentError:
            pass

        addresses.extend(local_addresses)

        location = ",".join(addresses)
        self.log("Tub location set to %s" % location)
        self.tub.setLocation(location)
        return self.tub

    def tub_ready(self):
        # called when the Tub is available for registerReference
        pass

    def when_tub_ready(self):
        return self._tub_ready_observerlist.when_fired()

    def add_service(self, s):
        s.setServiceParent(self)
        return s

