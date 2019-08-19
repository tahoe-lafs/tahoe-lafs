"""
This module contains classes and functions to implement and manage
a node for Tahoe-LAFS.
"""
import datetime
import os.path
import re
import types
import errno
import ConfigParser
import tempfile
from io import BytesIO
from base64 import b32decode, b32encode

from twisted.python import log as twlog
from twisted.application import service
from twisted.python.failure import Failure
from foolscap.api import Tub, app_versions
import foolscap.logging.log
from allmydata.version_checks import get_package_versions, get_package_versions_string
from allmydata.util import log
from allmydata.util import fileutil, iputil
from allmydata.util.assertutil import _assert
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.util.encodingutil import get_filesystem_encoding, quote_output
from allmydata.util import configutil

def _common_valid_config():
    return configutil.ValidConfiguration({
        "connections": (
            "tcp",
        ),
        "node": (
            "log_gatherer.furl",
            "nickname",
            "reveal-ip-address",
            "tempdir",
            "timeout.disconnect",
            "timeout.keepalive",
            "tub.location",
            "tub.port",
            "web.port",
            "web.static",
        ),
        "i2p": (
            "enabled",
            "i2p.configdir",
            "i2p.executable",
            "launch",
            "sam.port",
            "dest",
            "dest.port",
            "dest.private_key_file",
        ),
        "tor": (
            "control.port",
            "enabled",
            "launch",
            "socks.port",
            "tor.executable",
            "onion",
            "onion.local_port",
            "onion.external_port",
            "onion.private_key_file",
        ),
    })

# Add our application versions to the data that Foolscap's LogPublisher
# reports.
for thing, things_version in get_package_versions().iteritems():
    app_versions.add_version(thing, str(things_version))

# group 1 will be addr (dotted quad string), group 3 if any will be portnum (string)
ADDR_RE = re.compile("^([1-9][0-9]*\.[1-9][0-9]*\.[1-9][0-9]*\.[1-9][0-9]*)(:([1-9][0-9]*))?$")

# this is put into README in new node-directories (for client and introducers)
PRIV_README = """
This directory contains files which contain private data for the Tahoe node,
such as private keys.  On Unix-like systems, the permissions on this directory
are set to disallow users other than its owner from reading the contents of
the files.   See the 'configuration.rst' documentation file for details.
"""


def formatTimeTahoeStyle(self, when):
    """
    Format the given (UTC) timestamp in the way Tahoe-LAFS expects it,
    for example: 2007-10-12 00:26:28.566Z

    :param when: UTC POSIX timestamp
    :type when: float
    :returns: datetime.datetime
    """
    d = datetime.datetime.utcfromtimestamp(when)
    if d.microsecond:
        return d.isoformat(" ")[:-3]+"Z"
    return d.isoformat(" ") + ".000Z"

PRIV_README = """
This directory contains files which contain private data for the Tahoe node,
such as private keys.  On Unix-like systems, the permissions on this directory
are set to disallow users other than its owner from reading the contents of
the files.   See the 'configuration.rst' documentation file for details."""

class _None(object):
    """
    This class is to be used as a marker in get_config()
    """
    pass

class MissingConfigEntry(Exception):
    """ A required config entry was not found. """

class OldConfigError(Exception):
    """ An obsolete config file was found. See
    docs/historical/configuration.rst. """
    def __str__(self):
        return ("Found pre-Tahoe-LAFS-v1.3 configuration file(s):\n"
                "%s\n"
                "See docs/historical/configuration.rst."
                % "\n".join([quote_output(fname) for fname in self.args[0]]))

class OldConfigOptionError(Exception):
    """Indicate that outdated configuration options are being used."""
    pass

class UnescapedHashError(Exception):
    """Indicate that a configuration entry contains an unescaped '#' character."""
    def __str__(self):
        return ("The configuration entry %s contained an unescaped '#' character."
                % quote_output("[%s]%s = %s" % self.args))

class PrivacyError(Exception):
    """reveal-IP-address = false, but the node is configured in such a way
    that the IP address could be revealed"""


def create_node_dir(basedir, readme_text):
    """
    Create new new 'node directory' at 'basedir'. This includes a
    'private' subdirectory. If basedir (and privdir) already exists,
    nothing is done.

    :param readme_text: text to put in <basedir>/private/README
    """
    if not os.path.exists(basedir):
        fileutil.make_dirs(basedir)
    privdir = os.path.join(basedir, "private")
    if not os.path.exists(privdir):
        fileutil.make_dirs(privdir, 0o700)
        with open(os.path.join(privdir, 'README'), 'w') as f:
            f.write(readme_text)


def read_config(basedir, portnumfile, generated_files=[], _valid_config=None):
    """
    Read and validate configuration.

    :param unicode basedir: directory where configuration data begins

    :param unicode portnumfile: filename fragment for "port number" files

    :param list generated_files: a list of automatically-generated
        configuration files.

    :param ValidConfiguration _valid_config: (internal use, optional) a
        structure defining valid configuration sections and keys

    :returns: :class:`allmydata.node._Config` instance
    """
    basedir = abspath_expanduser_unicode(unicode(basedir))
    if _valid_config is None:
        _valid_config = _common_valid_config()

    # complain if there's bad stuff in the config dir
    _error_about_old_config_files(basedir, generated_files)

    # canonicalize the portnum file
    portnumfile = os.path.join(basedir, portnumfile)

    # (try to) read the main config file
    config_fname = os.path.join(basedir, "tahoe.cfg")
    parser = ConfigParser.SafeConfigParser()
    try:
        parser = configutil.get_config(config_fname)
    except EnvironmentError as e:
        if e.errno != errno.ENOENT:
            raise

    configutil.validate_config(config_fname, parser, _valid_config)

    # make sure we have a private configuration area
    fileutil.make_dirs(os.path.join(basedir, "private"), 0o700)

    return _Config(parser, portnumfile, basedir, config_fname)


def config_from_string(basedir, portnumfile, config_str, _valid_config=None):
    """
    load and validate configuration from in-memory string
    """
    if _valid_config is None:
        _valid_config = _common_valid_config()

    # load configuration from in-memory string
    parser = ConfigParser.SafeConfigParser()
    parser.readfp(BytesIO(config_str))

    fname = "<in-memory>"
    configutil.validate_config(fname, parser, _valid_config)
    return _Config(parser, portnumfile, basedir, fname)


def get_app_versions():
    """
    :returns: dict of versions important to Foolscap
    """
    return dict(app_versions.versions)


def _error_about_old_config_files(basedir, generated_files):
    """
    If any old configuration files are detected, raise
    OldConfigError.
    """
    oldfnames = set()
    old_names = [
        'nickname', 'webport', 'keepalive_timeout', 'log_gatherer.furl',
        'disconnect_timeout', 'advertised_ip_addresses', 'introducer.furl',
        'helper.furl', 'key_generator.furl', 'stats_gatherer.furl',
        'no_storage', 'readonly_storage', 'sizelimit',
        'debug_discard_storage', 'run_helper'
    ]
    for fn in generated_files:
        old_names.remove(fn)
    for name in old_names:
        fullfname = os.path.join(basedir, name)
        if os.path.exists(fullfname):
            oldfnames.add(fullfname)
    if oldfnames:
        e = OldConfigError(oldfnames)
        twlog.msg(e)
        raise e


class _Config(object):
    """
    Manages configuration of a Tahoe 'node directory'.

    Note: all this code and functionality was formerly in the Node
    class; names and funtionality have been kept the same while moving
    the code. It probably makes sense for several of these APIs to
    have better names.
    """

    def __init__(self, configparser, portnum_fname, basedir, config_fname):
        """
        :param configparser: a ConfigParser instance

        :param portnum_fname: filename to use for the port-number file
           (a relative path inside basedir)

        :param basedir: path to our "node directory", inside which all
           configuration is managed

        :param config_fname: the pathname actually used to create the
            configparser (might be 'fake' if using in-memory data)
        """
        self.portnum_fname = portnum_fname
        self._basedir = abspath_expanduser_unicode(unicode(basedir))
        self._config_fname = config_fname
        self.config = configparser

        nickname_utf8 = self.get_config("node", "nickname", "<unspecified>")
        self.nickname = nickname_utf8.decode("utf-8")
        assert type(self.nickname) is unicode

    def validate(self, valid_config_sections):
        configutil.validate_config(self._config_fname, self.config, valid_config_sections)

    def write_config_file(self, name, value, mode="w"):
        """
        writes the given 'value' into a file called 'name' in the config
        directory
        """
        fn = os.path.join(self._basedir, name)
        try:
            fileutil.write(fn, value, mode)
        except EnvironmentError:
            log.err(
                Failure(),
                "Unable to write config file '{}'".format(fn),
            )

    def items(self, section, default=_None):
        try:
            return self.config.items(section)
        except ConfigParser.NoSectionError:
            if default is _None:
                raise
            return default

    def get_config(self, section, option, default=_None, boolean=False):
        try:
            if boolean:
                return self.config.getboolean(section, option)

            item = self.config.get(section, option)
            if option.endswith(".furl") and self._contains_unescaped_hash(item):
                raise UnescapedHashError(section, option, item)

            return item
        except (ConfigParser.NoOptionError, ConfigParser.NoSectionError):
            if default is _None:
                raise MissingConfigEntry(
                    "{} is missing the [{}]{} entry".format(
                        quote_output(self._config_fname),
                        section,
                        option,
                    )
                )
            return default

    def get_config_from_file(self, name, required=False):
        """Get the (string) contents of a config file, or None if the file
        did not exist. If required=True, raise an exception rather than
        returning None. Any leading or trailing whitespace will be stripped
        from the data."""
        fn = os.path.join(self._basedir, name)
        try:
            return fileutil.read(fn).strip()
        except EnvironmentError as e:
            if e.errno != errno.ENOENT:
                raise  # we only care about "file doesn't exist"
            if not required:
                return None
            raise

    def get_or_create_private_config(self, name, default=_None):
        """Try to get the (string) contents of a private config file (which
        is a config file that resides within the subdirectory named
        'private'), and return it. Any leading or trailing whitespace will be
        stripped from the data.

        If the file does not exist, and default is not given, report an error.
        If the file does not exist and a default is specified, try to create
        it using that default, and then return the value that was written.
        If 'default' is a string, use it as a default value. If not, treat it
        as a zero-argument callable that is expected to return a string.
        """
        privname = os.path.join(self._basedir, "private", name)
        try:
            value = fileutil.read(privname)
        except EnvironmentError as e:
            if e.errno != errno.ENOENT:
                raise  # we only care about "file doesn't exist"
            if default is _None:
                raise MissingConfigEntry("The required configuration file %s is missing."
                                         % (quote_output(privname),))
            if isinstance(default, basestring):
                value = default
            else:
                value = default()
            fileutil.write(privname, value)
        return value.strip()

    def write_private_config(self, name, value):
        """Write the (string) contents of a private config file (which is a
        config file that resides within the subdirectory named 'private'), and
        return it.
        """
        privname = os.path.join(self._basedir, "private", name)
        with open(privname, "w") as f:
            f.write(value)

    def get_private_config(self, name, default=_None):
        """Read the (string) contents of a private config file (which is a
        config file that resides within the subdirectory named 'private'),
        and return it. Return a default, or raise an error if one was not
        given.
        """
        privname = os.path.join(self._basedir, "private", name)
        try:
            return fileutil.read(privname).strip()
        except EnvironmentError as e:
            if e.errno != errno.ENOENT:
                raise  # we only care about "file doesn't exist"
            if default is _None:
                raise MissingConfigEntry("The required configuration file %s is missing."
                                         % (quote_output(privname),))
            return default

    def get_private_path(self, *args):
        """
        returns an absolute path inside the 'private' directory with any
        extra args join()-ed
        """
        return os.path.join(self._basedir, "private", *args)

    def get_config_path(self, *args):
        """
        returns an absolute path inside the config directory with any
        extra args join()-ed
        """
        # note: we re-expand here (_basedir already went through this
        # expanduser function) in case the path we're being asked for
        # has embedded ".."'s in it
        return abspath_expanduser_unicode(
            os.path.join(self._basedir, *args)
        )

    @staticmethod
    def _contains_unescaped_hash(item):
        characters = iter(item)
        for c in characters:
            if c == '\\':
                characters.next()
            elif c == '#':
                return True

        return False


def create_tub_options(config):
    """
    :param config: a _Config instance

    :returns: dict containing all Foolscap Tub-related options,
        overriding defaults with appropriate config from `config`
        instance.
    """
    # We can't unify the camelCase vs. dashed-name divide here,
    # because these are options for Foolscap
    tub_options = {
        "logLocalFailures": True,
        "logRemoteFailures": True,
        "expose-remote-exception-types": False,
        "accept-gifts": False,
    }

    # see #521 for a discussion of how to pick these timeout values.
    keepalive_timeout_s = config.get_config("node", "timeout.keepalive", "")
    if keepalive_timeout_s:
        tub_options["keepaliveTimeout"] = int(keepalive_timeout_s)
    disconnect_timeout_s = config.get_config("node", "timeout.disconnect", "")
    if disconnect_timeout_s:
        # N.B.: this is in seconds, so use "1800" to get 30min
        tub_options["disconnectTimeout"] = int(disconnect_timeout_s)
    return tub_options


def _make_tcp_handler():
    """
    :returns: a Foolscap default TCP handler
    """
    # this is always available
    from foolscap.connections.tcp import default
    return default()


def create_connection_handlers(reactor, config, i2p_provider, tor_provider):
    """
    :returns: 2-tuple of default_connection_handlers, foolscap_connection_handlers
    """
    reveal_ip = config.get_config("node", "reveal-IP-address", True, boolean=True)

    # We store handlers for everything. None means we were unable to
    # create that handler, so hints which want it will be ignored.
    handlers = foolscap_connection_handlers = {
        "tcp": _make_tcp_handler(),
        "tor": tor_provider.get_tor_handler(),
        "i2p": i2p_provider.get_i2p_handler(),
        }
    log.msg(
        format="built Foolscap connection handlers for: %(known_handlers)s",
        known_handlers=sorted([k for k,v in handlers.items() if v]),
        facility="tahoe.node",
        umid="PuLh8g",
    )

    # then we remember the default mappings from tahoe.cfg
    default_connection_handlers = {"tor": "tor", "i2p": "i2p"}
    tcp_handler_name = config.get_config("connections", "tcp", "tcp").lower()
    if tcp_handler_name == "disabled":
        default_connection_handlers["tcp"] = None
    else:
        if tcp_handler_name not in handlers:
            raise ValueError(
                "'tahoe.cfg [connections] tcp=' uses "
                "unknown handler type '{}'".format(
                    tcp_handler_name
                )
            )
        if not handlers[tcp_handler_name]:
            raise ValueError(
                "'tahoe.cfg [connections] tcp=' uses "
                "unavailable/unimportable handler type '{}'. "
                "Please pip install tahoe-lafs[{}] to fix.".format(
                    tcp_handler_name,
                    tcp_handler_name,
                )
            )
        default_connection_handlers["tcp"] = tcp_handler_name

    if not reveal_ip:
        if default_connection_handlers.get("tcp") == "tcp":
            raise PrivacyError("tcp = tcp, must be set to 'tor' or 'disabled'")
    return default_connection_handlers, foolscap_connection_handlers



def create_tub(tub_options, default_connection_handlers, foolscap_connection_handlers,
               handler_overrides={}, **kwargs):
    """
    Create a Tub with the right options and handlers. It will be
    ephemeral unless the caller provides certFile= in kwargs

    :param handler_overrides: anything in this will override anything
        in `default_connection_handlers` for just this call.

    :param dict tub_options: every key-value pair in here will be set in
        the new Tub via `Tub.setOption`
    """
    tub = Tub(**kwargs)
    for (name, value) in tub_options.items():
        tub.setOption(name, value)
    handlers = default_connection_handlers.copy()
    handlers.update(handler_overrides)
    tub.removeAllConnectionHintHandlers()
    for hint_type, handler_name in handlers.items():
        handler = foolscap_connection_handlers.get(handler_name)
        if handler:
            tub.addConnectionHintHandler(hint_type, handler)
    return tub


def _convert_tub_port(s):
    """
    :returns: a proper Twisted endpoint string like (`tcp:X`) is `s`
        is a bare number, or returns `s` as-is
    """
    if re.search(r'^\d+$', s):
        return "tcp:{}".format(int(s))
    return s


def _tub_portlocation(config):
    """
    :returns: None or tuple of (port, location) for the main tub based
        on the given configuration. May raise ValueError or PrivacyError
        if there are problems with the config
    """
    cfg_tubport = config.get_config("node", "tub.port", None)
    cfg_location = config.get_config("node", "tub.location", None)
    reveal_ip = config.get_config("node", "reveal-IP-address", True, boolean=True)
    tubport_disabled = False

    if cfg_tubport is not None:
        cfg_tubport = cfg_tubport.strip()
        if cfg_tubport == "":
            raise ValueError("tub.port must not be empty")
        if cfg_tubport == "disabled":
            tubport_disabled = True

    location_disabled = False
    if cfg_location is not None:
        cfg_location = cfg_location.strip()
        if cfg_location == "":
            raise ValueError("tub.location must not be empty")
        if cfg_location == "disabled":
            location_disabled = True

    if tubport_disabled and location_disabled:
        return None
    if tubport_disabled and not location_disabled:
        raise ValueError("tub.port is disabled, but not tub.location")
    if location_disabled and not tubport_disabled:
        raise ValueError("tub.location is disabled, but not tub.port")

    if cfg_tubport is None:
        # For 'tub.port', tahoe.cfg overrides the individual file on
        # disk. So only read config.portnum_fname if tahoe.cfg doesn't
        # provide a value.
        if os.path.exists(config.portnum_fname):
            file_tubport = fileutil.read(config.portnum_fname).strip()
            tubport = _convert_tub_port(file_tubport)
        else:
            tubport = "tcp:%d" % iputil.allocate_tcp_port()
            fileutil.write_atomically(config.portnum_fname, tubport + "\n",
                                      mode="")
    else:
        tubport = _convert_tub_port(cfg_tubport)

    for port in tubport.split(","):
        if port in ("0", "tcp:0"):
            raise ValueError("tub.port cannot be 0: you must choose")

    if cfg_location is None:
        cfg_location = "AUTO"

    local_portnum = None # needed to hush lgtm.com static analyzer
    # Replace the location "AUTO", if present, with the detected local
    # addresses. Don't probe for local addresses unless necessary.
    split_location = cfg_location.split(",")
    if "AUTO" in split_location:
        if not reveal_ip:
            raise PrivacyError("tub.location uses AUTO")
        local_addresses = iputil.get_local_addresses_sync()
        # tubport must be like "tcp:12345" or "tcp:12345:morestuff"
        local_portnum = int(tubport.split(":")[1])
    new_locations = []
    for loc in split_location:
        if loc == "AUTO":
            new_locations.extend(["tcp:%s:%d" % (ip, local_portnum)
                                  for ip in local_addresses])
        else:
            if not reveal_ip:
                # Legacy hints are "host:port". We use Foolscap's utility
                # function to convert all hints into the modern format
                # ("tcp:host:port") because that's what the receiving
                # client will probably do. We test the converted hint for
                # TCP-ness, but publish the original hint because that
                # was the user's intent.
                from foolscap.connections.tcp import convert_legacy_hint
                converted_hint = convert_legacy_hint(loc)
                hint_type = converted_hint.split(":")[0]
                if hint_type == "tcp":
                    raise PrivacyError("tub.location includes tcp: hint")
            new_locations.append(loc)
    location = ",".join(new_locations)

    return tubport, location


def create_main_tub(config, tub_options,
                    default_connection_handlers, foolscap_connection_handlers,
                    i2p_provider, tor_provider,
                    handler_overrides={}, cert_filename="node.pem"):
    """
    Creates a 'main' Foolscap Tub, typically for use as the top-level
    access point for a running Node.

    :param Config: a `_Config` instance

    :param dict tub_options: any options to change in the tub

    :param default_connection_handlers: default Foolscap connection
        handlers

    :param foolscap_connection_handlers: Foolscap connection
        handlers for this tub

    :param i2p_provider: None, or a _Provider instance if I2P is
        installed.

    :param tor_provider: None, or a _Provider instance if txtorcon +
        Tor are installed.
    """
    portlocation = _tub_portlocation(config)

    certfile = config.get_private_path("node.pem")  # FIXME? "node.pem" was the CERTFILE option/thing
    tub = create_tub(tub_options, default_connection_handlers, foolscap_connection_handlers,
                     handler_overrides=handler_overrides, certFile=certfile)

    if portlocation:
        tubport, location = portlocation
        for port in tubport.split(","):
            if port == "listen:i2p":
                # the I2P provider will read its section of tahoe.cfg and
                # return either a fully-formed Endpoint, or a descriptor
                # that will create one, so we don't have to stuff all the
                # options into the tub.port string (which would need a lot
                # of escaping)
                port_or_endpoint = i2p_provider.get_listener()
            elif port == "listen:tor":
                port_or_endpoint = tor_provider.get_listener()
            else:
                port_or_endpoint = port
            tub.listenOn(port_or_endpoint)
        tub.setLocation(location)
        log.msg("Tub location set to %s" % (location,))
        # the Tub is now ready for tub.registerReference()
    else:
        log.msg("Tub is not listening")

    return tub


def create_control_tub():
    """
    Creates a Foolscap Tub for use by the control port. This is a
    localhost-only ephemeral Tub, with no control over the listening
    port or location
    """
    control_tub = Tub()
    portnum = iputil.listenOnUnused(control_tub)
    log.msg("Control Tub location set to 127.0.0.1:%s" % (portnum,))
    return control_tub


class Node(service.MultiService):
    """
    This class implements common functionality of both Client nodes and Introducer nodes.
    """
    NODETYPE = "unknown NODETYPE"
    CERTFILE = "node.pem"
    GENERATED_FILES = []

    def __init__(self, config, main_tub, control_tub, i2p_provider, tor_provider):
        """
        Initialize the node with the given configuration. Its base directory
        is the current directory by default.
        """
        service.MultiService.__init__(self)

        self.config = config
        self.get_config = config.get_config # XXX stopgap
        self.nickname = config.nickname # XXX stopgap

        # this can go away once Client.init_client_storage_broker is moved into create_client()
        # (tests sometimes have None here)
        self._i2p_provider = i2p_provider
        self._tor_provider = tor_provider

        self.init_tempdir()

        self.create_log_tub()
        self.logSource = "Node"
        self.setup_logging()

        self.tub = main_tub
        if self.tub is not None:
            self.nodeid = b32decode(self.tub.tubID.upper())  # binary format
            self.short_nodeid = b32encode(self.nodeid).lower()[:8]  # for printing
            self.config.write_config_file("my_nodeid", b32encode(self.nodeid).lower() + "\n")
            self.tub.setServiceParent(self)
        else:
            self.nodeid = self.short_nodeid = None

        self.control_tub = control_tub
        if self.control_tub is not None:
            self.control_tub.setServiceParent(self)

        self.log("Node constructed. " + get_package_versions_string())
        iputil.increase_rlimits()

    def _is_tub_listening(self):
        """
        :returns: True if the main tub is listening
        """
        return len(self.tub.getListeners()) > 0

    def init_tempdir(self):
        """
        Initialize/create a directory for temporary files.
        """
        tempdir_config = self.config.get_config("node", "tempdir", "tmp").decode('utf-8')
        tempdir = self.config.get_config_path(tempdir_config)
        if not os.path.exists(tempdir):
            fileutil.make_dirs(tempdir)
        tempfile.tempdir = tempdir
        # this should cause twisted.web.http (which uses
        # tempfile.TemporaryFile) to put large request bodies in the given
        # directory. Without this, the default temp dir is usually /tmp/,
        # which is frequently too small.
        temp_fd, test_name = tempfile.mkstemp()
        _assert(os.path.dirname(test_name) == tempdir, test_name, tempdir)
        os.close(temp_fd)  # avoid leak of unneeded fd

    # pull this outside of Node's __init__ too, see:
    # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2948
    def create_log_tub(self):
        # The logport uses a localhost-only ephemeral Tub, with no control
        # over the listening port or location. This might change if we
        # discover a compelling reason for it in the future (e.g. being able
        # to use "flogtool tail" against a remote server), but for now I
        # think we can live without it.
        self.log_tub = Tub()
        portnum = iputil.listenOnUnused(self.log_tub)
        self.log("Log Tub location set to 127.0.0.1:%s" % (portnum,))
        self.log_tub.setServiceParent(self)

    def startService(self):
        # Note: this class can be started and stopped at most once.
        self.log("Node.startService")
        # Record the process id in the twisted log, after startService()
        # (__init__ is called before fork(), but startService is called
        # after). Note that Foolscap logs handle pid-logging by itself, no
        # need to send a pid to the foolscap log here.
        twlog.msg("My pid: %s" % os.getpid())
        try:
            os.chmod("twistd.pid", 0o644)
        except EnvironmentError:
            pass

        service.MultiService.startService(self)
        self.log("%s running" % self.NODETYPE)
        twlog.msg("%s running" % self.NODETYPE)

    def stopService(self):
        self.log("Node.stopService")
        return service.MultiService.stopService(self)

    def shutdown(self):
        """Shut down the node. Returns a Deferred that fires (with None) when
        it finally stops kicking."""
        self.log("Node.shutdown")
        return self.stopService()

    def setup_logging(self):
        # we replace the formatTime() method of the log observer that
        # twistd set up for us, with a method that uses our preferred
        # timestamp format.
        for o in twlog.theLogPublisher.observers:
            # o might be a FileLogObserver's .emit method
            if type(o) is type(self.setup_logging): # bound method
                ob = o.im_self
                if isinstance(ob, twlog.FileLogObserver):
                    newmeth = types.UnboundMethodType(formatTimeTahoeStyle, ob, ob.__class__)
                    ob.formatTime = newmeth
        # TODO: twisted >2.5.0 offers maxRotatedFiles=50

        lgfurl_file = self.config.get_private_path("logport.furl").encode(get_filesystem_encoding())
        if os.path.exists(lgfurl_file):
            os.remove(lgfurl_file)
        self.log_tub.setOption("logport-furlfile", lgfurl_file)
        lgfurl = self.config.get_config("node", "log_gatherer.furl", "")
        if lgfurl:
            # this is in addition to the contents of log-gatherer-furlfile
            self.log_tub.setOption("log-gatherer-furl", lgfurl)
        self.log_tub.setOption("log-gatherer-furlfile",
                               self.config.get_config_path("log_gatherer.furl"))

        incident_dir = self.config.get_config_path("logs", "incidents")
        foolscap.logging.log.setLogDir(incident_dir.encode(get_filesystem_encoding()))
        twlog.msg("Foolscap logging initialized")
        twlog.msg("Note to developers: twistd.log does not receive very much.")
        twlog.msg("Use 'flogtool tail -c NODEDIR/private/logport.furl' instead")
        twlog.msg("and read docs/logging.rst")

    def log(self, *args, **kwargs):
        return log.msg(*args, **kwargs)
