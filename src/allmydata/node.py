
import datetime, os.path, re, types, ConfigParser, tempfile
from base64 import b32decode, b32encode

from twisted.python import log as twlog
from twisted.application import service
from twisted.internet import defer, reactor
from foolscap import Tub, eventual
import foolscap.logging.log
from allmydata import get_package_versions, get_package_versions_string
from allmydata.util import log
from allmydata.util import fileutil, iputil, observer
from allmydata.util.assertutil import precondition, _assert

from foolscap.logging import app_versions

# Add our application versions to the data that Foolscap's LogPublisher
# reports.
for thing, things_version in get_package_versions().iteritems():
    app_versions.add_version(thing, str(things_version))

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

PRIV_README="""
This directory contains files which contain private data for the Tahoe node,
such as private keys.  On Unix-like systems, the permissions on this directory
are set to disallow users other than its owner from reading the contents of
the files.   See the 'configuration.txt' documentation file for details."""

class _None: # used as a marker in get_config()
    pass

class MissingConfigEntry(Exception):
    pass

class Node(service.MultiService):
    # this implements common functionality of both Client nodes and Introducer
    # nodes.
    NODETYPE = "unknown NODETYPE"
    PORTNUMFILE = None
    CERTFILE = "node.pem"

    def __init__(self, basedir="."):
        service.MultiService.__init__(self)
        self.basedir = os.path.abspath(basedir)
        self._portnumfile = os.path.join(self.basedir, self.PORTNUMFILE)
        self._tub_ready_observerlist = observer.OneShotObserverList()
        fileutil.make_dirs(os.path.join(self.basedir, "private"), 0700)
        open(os.path.join(self.basedir, "private", "README"), "w").write(PRIV_README)

        # creates self.config, populates from distinct files if necessary
        self.read_config()

        nickname_utf8 = self.get_config("node", "nickname", "<unspecified>")
        self.nickname = nickname_utf8.decode("utf-8")

        self.init_tempdir()
        self.create_tub()
        self.logSource="Node"

        self.setup_ssh()
        self.setup_logging()
        self.log("Node constructed. " + get_package_versions_string())
        iputil.increase_rlimits()

    def init_tempdir(self):
        local_tempdir = "tmp" # default is NODEDIR/tmp/
        tempdir = self.get_config("node", "tempdir", local_tempdir)
        tempdir = os.path.join(self.basedir, tempdir)
        if not os.path.exists(tempdir):
            fileutil.make_dirs(tempdir)
        tempfile.tempdir = os.path.abspath(tempdir)
        # this should cause twisted.web.http (which uses
        # tempfile.TemporaryFile) to put large request bodies in the given
        # directory. Without this, the default temp dir is usually /tmp/,
        # which is frequently too small.
        test_name = tempfile.mktemp()
        _assert(os.path.dirname(test_name) == tempdir, test_name, tempdir)

    def get_config(self, section, option, default=_None, boolean=False):
        try:
            if boolean:
                return self.config.getboolean(section, option)
            return self.config.get(section, option)
        except (ConfigParser.NoOptionError, ConfigParser.NoSectionError):
            if default is _None:
                fn = os.path.join(self.basedir, "tahoe.cfg")
                raise MissingConfigEntry("%s is missing the [%s]%s entry"
                                         % (fn, section, option))
            return default

    def set_config(self, section, option, value):
        if not self.config.has_section(section):
            self.config.add_section(section)
        self.config.set(section, option, value)
        assert self.config.get(section, option) == value

    def read_config(self):
        self.config = ConfigParser.SafeConfigParser()
        self.config.read([os.path.join(self.basedir, "tahoe.cfg")])
        self.read_old_config_files()

    def read_old_config_files(self):
        # backwards-compatibility: individual files will override the
        # contents of tahoe.cfg
        copy = self._copy_config_from_file

        copy("nickname", "node", "nickname")
        copy("webport", "node", "web.port")

        cfg_tubport = self.get_config("node", "tub.port", "")
        if not cfg_tubport:
            # For 'tub.port', tahoe.cfg overrides the individual file on
            # disk. So only read self._portnumfile is tahoe.cfg doesn't
            # provide a value.
            try:
                file_tubport = open(self._portnumfile, "rU").read().strip()
                self.set_config("node", "tub.port", file_tubport)
            except EnvironmentError:
                pass

        copy("keepalive_timeout", "node", "timeout.keepalive")
        copy("disconnect_timeout", "node", "timeout.disconnect")

    def _copy_config_from_file(self, config_filename, section, keyname):
        s = self.get_config_from_file(config_filename)
        if s is not None:
            self.set_config(section, keyname, s)

    def create_tub(self):
        certfile = os.path.join(self.basedir, "private", self.CERTFILE)
        self.tub = Tub(certFile=certfile)
        self.tub.setOption("logLocalFailures", True)
        self.tub.setOption("logRemoteFailures", True)

        # see #521 for a discussion of how to pick these timeout values.
        keepalive_timeout_s = self.get_config("node", "timeout.keepalive", "")
        if keepalive_timeout_s:
            self.tub.setOption("keepaliveTimeout", int(keepalive_timeout_s))
        disconnect_timeout_s = self.get_config("node", "timeout.disconnect", "")
        if disconnect_timeout_s:
            # N.B.: this is in seconds, so use "1800" to get 30min
            self.tub.setOption("disconnectTimeout", int(disconnect_timeout_s))

        self.nodeid = b32decode(self.tub.tubID.upper()) # binary format
        self.write_config("my_nodeid", b32encode(self.nodeid).lower() + "\n")
        self.short_nodeid = b32encode(self.nodeid).lower()[:8] # ready for printing

        tubport = self.get_config("node", "tub.port", "tcp:0")
        self.tub.listenOn(tubport)
        # we must wait until our service has started before we can find out
        # our IP address and thus do tub.setLocation, and we can't register
        # any services with the Tub until after that point
        self.tub.setServiceParent(self)

    def setup_ssh(self):
        ssh_port = self.get_config("node", "ssh.port", "")
        if ssh_port:
            ssh_keyfile = self.get_config("node", "ssh.authorized_keys_file")
            from allmydata import manhole
            m = manhole.AuthorizedKeysManhole(ssh_port, ssh_keyfile)
            m.setServiceParent(self)
            self.log("AuthorizedKeysManhole listening on %s" % ssh_port)

    def get_app_versions(self):
        # TODO: merge this with allmydata.get_package_versions
        return dict(app_versions.versions)

    def get_config_from_file(self, name, required=False):
        """Get the (string) contents of a config file, or None if the file
        did not exist. If required=True, raise an exception rather than
        returning None. Any leading or trailing whitespace will be stripped
        from the data."""
        fn = os.path.join(self.basedir, name)
        try:
            return open(fn, "r").read().strip()
        except EnvironmentError:
            if not required:
                return None
            raise

    def write_private_config(self, name, value):
        """Write the (string) contents of a private config file (which is a
        config file that resides within the subdirectory named 'private'), and
        return it. Any leading or trailing whitespace will be stripped from
        the data.
        """
        privname = os.path.join(self.basedir, "private", name)
        open(privname, "w").write(value.strip())

    def get_or_create_private_config(self, name, default):
        """Try to get the (string) contents of a private config file (which
        is a config file that resides within the subdirectory named
        'private'), and return it. Any leading or trailing whitespace will be
        stripped from the data.

        If the file does not exist, try to create it using default, and
        then return the value that was written. If 'default' is a string,
        use it as a default value. If not, treat it as a 0-argument callable
        which is expected to return a string.
        """
        privname = os.path.join("private", name)
        value = self.get_config_from_file(privname)
        if value is None:
            if isinstance(default, (str, unicode)):
                value = default
            else:
                value = default()
            fn = os.path.join(self.basedir, privname)
            try:
                open(fn, "w").write(value)
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

    def startService(self):
        # Note: this class can be started and stopped at most once.
        self.log("Node.startService")
        try:
            os.chmod("twistd.pid", 0644)
        except EnvironmentError:
            pass
        # Delay until the reactor is running.
        eventual.eventually(self._startService)

    def _startService(self):
        precondition(reactor.running)
        self.log("Node._startService")

        service.MultiService.startService(self)
        d = defer.succeed(None)
        d.addCallback(lambda res: iputil.get_local_addresses_async())
        d.addCallback(self._setup_tub)
        def _ready(res):
            self.log("%s running" % self.NODETYPE)
            self._tub_ready_observerlist.fire(self)
            return self
        d.addCallback(_ready)
        d.addErrback(self._service_startup_failed)

    def _service_startup_failed(self, failure):
        self.log('_startService() failed')
        log.err(failure)
        print "Node._startService failed, aborting"
        print failure
        #reactor.stop() # for unknown reasons, reactor.stop() isn't working.  [ ] TODO
        self.log('calling os.abort()')
        twlog.msg('calling os.abort()') # make sure it gets into twistd.log
        print "calling os.abort()"
        os.abort()

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
        # we replace the formatTime() method of the log observer that twistd
        # set up for us, with a method that uses better timestamps.
        for o in twlog.theLogPublisher.observers:
            # o might be a FileLogObserver's .emit method
            if type(o) is type(self.setup_logging): # bound method
                ob = o.im_self
                if isinstance(ob, twlog.FileLogObserver):
                    newmeth = types.UnboundMethodType(formatTimeTahoeStyle, ob, ob.__class__)
                    ob.formatTime = newmeth
        # TODO: twisted >2.5.0 offers maxRotatedFiles=50

        self.tub.setOption("logport-furlfile",
                           os.path.join(self.basedir, "private","logport.furl"))
        lgfurl = self.get_config("node", "log_gatherer.furl", "")
        if lgfurl:
            # this is in addition to the contents of log-gatherer-furlfile
            self.tub.setOption("log-gatherer-furl", lgfurl)
        self.tub.setOption("log-gatherer-furlfile",
                           os.path.join(self.basedir, "log_gatherer.furl"))
        self.tub.setOption("bridge-twisted-logs", True)
        incident_dir = os.path.join(self.basedir, "logs", "incidents")
        # this doesn't quite work yet: unit tests fail
        foolscap.logging.log.setLogDir(incident_dir)

    def log(self, *args, **kwargs):
        return log.msg(*args, **kwargs)

    def _setup_tub(self, local_addresses):
        # we can't get a dynamically-assigned portnum until our Tub is
        # running, which means after startService.
        l = self.tub.getListeners()[0]
        portnum = l.getPortnum()
        # record which port we're listening on, so we can grab the same one
        # next time
        open(self._portnumfile, "w").write("%d\n" % portnum)

        base_location = ",".join([ "%s:%d" % (addr, portnum)
                                   for addr in local_addresses ])
        location = self.get_config("node", "tub.location", base_location)
        self.log("Tub location set to %s" % location)
        self.tub.setLocation(location)

        return self.tub

    def when_tub_ready(self):
        return self._tub_ready_observerlist.when_fired()

    def add_service(self, s):
        s.setServiceParent(self)
        return s

