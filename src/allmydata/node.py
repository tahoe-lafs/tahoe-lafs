"""
This module contains classes and functions to implement and manage
a node for Tahoe-LAFS.

Ported to Python 3.
"""
from __future__ import annotations

from six import ensure_str, ensure_text

import json
import datetime
import os.path
import re
import types
import errno
from base64 import b32decode, b32encode
from errno import ENOENT, EPERM
from warnings import warn
from typing import Union, Iterable

import attr

# On Python 2 this will be the backported package.
import configparser

from twisted.python.filepath import (
    FilePath,
)
from twisted.python import log as twlog
from twisted.application import service
from twisted.python.failure import Failure
from foolscap.api import Tub

import foolscap.logging.log

from allmydata.util import log
from allmydata.util import fileutil, iputil
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.util.encodingutil import get_filesystem_encoding, quote_output
from allmydata.util import configutil
from allmydata.util.yamlutil import (
    safe_load,
)

from . import (
    __full_version__,
)
from .protocol_switch import create_tub_with_https_support


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
        return d.isoformat(ensure_str(" "))[:-3]+"Z"
    return d.isoformat(ensure_str(" ")) + ".000Z"

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
        readme_text = ensure_text(readme_text)
        with open(os.path.join(privdir, 'README'), 'w') as f:
            f.write(readme_text)


def read_config(basedir, portnumfile, generated_files: Iterable = (), _valid_config=None):
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
    basedir = abspath_expanduser_unicode(ensure_text(basedir))
    if _valid_config is None:
        _valid_config = _common_valid_config()

    # complain if there's bad stuff in the config dir
    _error_about_old_config_files(basedir, generated_files)

    # canonicalize the portnum file
    portnumfile = os.path.join(basedir, portnumfile)

    config_path = FilePath(basedir).child("tahoe.cfg")
    try:
        config_bytes = config_path.getContent()
    except EnvironmentError as e:
        if e.errno != errno.ENOENT:
            raise
        # The file is missing, just create empty ConfigParser.
        config_str = u""
    else:
        config_str = config_bytes.decode("utf-8-sig")

    return config_from_string(
        basedir,
        portnumfile,
        config_str,
        _valid_config,
        config_path,
    )


def config_from_string(basedir, portnumfile, config_str, _valid_config=None, fpath=None):
    """
    load and validate configuration from in-memory string
    """
    if _valid_config is None:
        _valid_config = _common_valid_config()

    if isinstance(config_str, bytes):
        config_str = config_str.decode("utf-8")

    # load configuration from in-memory string
    parser = configutil.get_config_from_string(config_str)

    configutil.validate_config(
        "<string>" if fpath is None else fpath.path,
        parser,
        _valid_config,
    )

    return _Config(
        parser,
        portnumfile,
        basedir,
        fpath,
        _valid_config,
    )


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


def ensure_text_and_abspath_expanduser_unicode(basedir: Union[bytes, str]) -> str:
    return abspath_expanduser_unicode(ensure_text(basedir))


@attr.s
class _Config(object):
    """
    Manages configuration of a Tahoe 'node directory'.

    Note: all this code and functionality was formerly in the Node
    class; names and funtionality have been kept the same while moving
    the code. It probably makes sense for several of these APIs to
    have better names.

    :ivar ConfigParser config: The actual configuration values.

    :ivar str portnum_fname: filename to use for the port-number file (a
        relative path inside basedir).

    :ivar str _basedir: path to our "node directory", inside which all
        configuration is managed.

    :ivar (FilePath|NoneType) config_path: The path actually used to create
        the configparser (might be ``None`` if using in-memory data).

    :ivar ValidConfiguration valid_config_sections: The validator for the
        values in this configuration.
    """
    config = attr.ib(validator=attr.validators.instance_of(configparser.ConfigParser))
    portnum_fname = attr.ib()
    _basedir = attr.ib(
        converter=ensure_text_and_abspath_expanduser_unicode,
    )  # type: str
    config_path = attr.ib(
        validator=attr.validators.optional(
            attr.validators.instance_of(FilePath),
        ),
    )
    valid_config_sections = attr.ib(
        default=configutil.ValidConfiguration.everything(),
        validator=attr.validators.instance_of(configutil.ValidConfiguration),
    )

    @property
    def nickname(self):
        nickname = self.get_config("node", "nickname", u"<unspecified>")
        assert isinstance(nickname, str)
        return nickname

    @property
    def _config_fname(self):
        if self.config_path is None:
            return "<string>"
        return self.config_path.path

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

    def enumerate_section(self, section):
        """
        returns a dict containing all items in a configuration section. an
        empty dict is returned if the section doesn't exist.
        """
        answer = dict()
        try:
            for k in self.config.options(section):
                answer[k] = self.config.get(section, k)
        except configparser.NoSectionError:
            pass
        return answer

    def items(self, section, default=_None):
        try:
            return self.config.items(section)
        except configparser.NoSectionError:
            if default is _None:
                raise
            return default

    def get_config(self, section, option, default=_None, boolean=False):
        try:
            if boolean:
                return self.config.getboolean(section, option)

            item = self.config.get(section, option)
            if option.endswith(".furl") and '#' in item:
                raise UnescapedHashError(section, option, item)

            return item
        except (configparser.NoOptionError, configparser.NoSectionError):
            if default is _None:
                raise MissingConfigEntry(
                    "{} is missing the [{}]{} entry".format(
                        quote_output(self._config_fname),
                        section,
                        option,
                    )
                )
            return default

    def set_config(self, section, option, value):
        """
        Set a config option in a section and re-write the tahoe.cfg file

        :param str section: The name of the section in which to set the
            option.

        :param str option: The name of the option to set.

        :param str value: The value of the option.

        :raise UnescapedHashError: If the option holds a fURL and there is a
            ``#`` in the value.
        """
        if option.endswith(".furl") and "#" in value:
            raise UnescapedHashError(section, option, value)

        copied_config = configutil.copy_config(self.config)
        configutil.set_config(copied_config, section, option, value)
        configutil.validate_config(
            self._config_fname,
            copied_config,
            self.valid_config_sections,
        )
        if self.config_path is not None:
            configutil.write_config(self.config_path, copied_config)
        self.config = copied_config

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
            value = fileutil.read(privname, mode="r")
        except EnvironmentError as e:
            if e.errno != errno.ENOENT:
                raise  # we only care about "file doesn't exist"
            if default is _None:
                raise MissingConfigEntry("The required configuration file %s is missing."
                                         % (quote_output(privname),))
            if isinstance(default, bytes):
                default = str(default, "utf-8")
            if isinstance(default, str):
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
        if isinstance(value, str):
            value = value.encode("utf-8")
        privname = os.path.join(self._basedir, "private", name)
        with open(privname, "wb") as f:
            f.write(value)

    def get_private_config(self, name, default=_None):
        """Read the (native string) contents of a private config file (a
        config file that resides within the subdirectory named 'private'),
        and return it. Return a default, or raise an error if one was not
        given.
        """
        privname = os.path.join(self._basedir, "private", name)
        try:
            return fileutil.read(privname, mode="r").strip()
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

        This exists for historical reasons. New code should ideally
        not call this because it makes it harder for e.g. a SQL-based
        _Config object to exist. Code that needs to call this method
        should probably be a _Config method itself. See
        e.g. get_grid_manager_certificates()
        """
        return os.path.join(self._basedir, "private", *args)

    def get_config_path(self, *args):
        """
        returns an absolute path inside the config directory with any
        extra args join()-ed

        This exists for historical reasons. New code should ideally
        not call this because it makes it harder for e.g. a SQL-based
        _Config object to exist. Code that needs to call this method
        should probably be a _Config method itself. See
        e.g. get_grid_manager_certificates()
        """
        # note: we re-expand here (_basedir already went through this
        # expanduser function) in case the path we're being asked for
        # has embedded ".."'s in it
        return abspath_expanduser_unicode(
            os.path.join(self._basedir, *args)
        )

    def get_grid_manager_certificates(self):
        """
        Load all Grid Manager certificates in the config.

        :returns: A list of all certificates. An empty list is
            returned if there are none.
        """
        grid_manager_certificates = []

        cert_fnames = list(self.enumerate_section("grid_manager_certificates").values())
        for fname in cert_fnames:
            fname = self.get_config_path(fname)
            if not os.path.exists(fname):
                raise ValueError(
                    "Grid Manager certificate file '{}' doesn't exist".format(
                        fname
                    )
                )
            with open(fname, 'r') as f:
                cert = json.load(f)
            if set(cert.keys()) != {"certificate", "signature"}:
                raise ValueError(
                    "Unknown key in Grid Manager certificate '{}'".format(
                        fname
                    )
                )
            grid_manager_certificates.append(cert)
        return grid_manager_certificates

    def get_introducer_configuration(self):
        """
        Get configuration for introducers.

        :return {unicode: (unicode, FilePath)}: A mapping from introducer
            petname to a tuple of the introducer's fURL and local cache path.
        """
        introducers_yaml_filename = self.get_private_path("introducers.yaml")
        introducers_filepath = FilePath(introducers_yaml_filename)

        def get_cache_filepath(petname):
            return FilePath(
                self.get_private_path("introducer_{}_cache.yaml".format(petname)),
            )

        try:
            with introducers_filepath.open() as f:
                introducers_yaml = safe_load(f)
                if introducers_yaml is None:
                    raise EnvironmentError(
                        EPERM,
                        "Can't read '{}'".format(introducers_yaml_filename),
                        introducers_yaml_filename,
                    )
                introducers = {
                    petname: config["furl"]
                    for petname, config
                    in introducers_yaml.get("introducers", {}).items()
                }
                non_strs = list(
                    k
                    for k
                    in introducers.keys()
                    if not isinstance(k, str)
                )
                if non_strs:
                    raise TypeError(
                        "Introducer petnames {!r} should have been str".format(
                            non_strs,
                        ),
                    )
                non_strs = list(
                    v
                    for v
                    in introducers.values()
                    if not isinstance(v, str)
                )
                if non_strs:
                    raise TypeError(
                        "Introducer fURLs {!r} should have been str".format(
                            non_strs,
                        ),
                    )
                log.msg(
                    "found {} introducers in {!r}".format(
                        len(introducers),
                        introducers_yaml_filename,
                    )
                )
        except EnvironmentError as e:
            if e.errno != ENOENT:
                raise
            introducers = {}

        # supported the deprecated [client]introducer.furl item in tahoe.cfg
        tahoe_cfg_introducer_furl = self.get_config("client", "introducer.furl", None)
        if tahoe_cfg_introducer_furl == "None":
            raise ValueError(
                "tahoe.cfg has invalid 'introducer.furl = None':"
                " to disable it omit the key entirely"
            )
        if tahoe_cfg_introducer_furl:
            warn(
                "tahoe.cfg [client]introducer.furl is deprecated; "
                "use private/introducers.yaml instead.",
                category=DeprecationWarning,
                stacklevel=-1,
            )
            if "default" in introducers:
                raise ValueError(
                    "'default' introducer furl cannot be specified in tahoe.cfg and introducers.yaml;"
                    " please fix impossible configuration."
                )
            introducers['default'] = tahoe_cfg_introducer_furl

        return {
            petname: (furl, get_cache_filepath(petname))
            for (petname, furl)
            in introducers.items()
        }


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


def create_default_connection_handlers(config, handlers):
    """
    :return: A dictionary giving the default connection handlers.  The keys
        are strings like "tcp" and the values are strings like "tor" or
        ``None``.
    """
    reveal_ip = config.get_config("node", "reveal-IP-address", True, boolean=True)

    # Remember the default mappings from tahoe.cfg
    default_connection_handlers = {
        name: name
        for name
        in handlers
    }
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
            raise PrivacyError(
                "Privacy requested with `reveal-IP-address = false` "
                "but `tcp = tcp` conflicts with this.",
            )
    return default_connection_handlers


def create_connection_handlers(config, i2p_provider, tor_provider):
    """
    :returns: 2-tuple of default_connection_handlers, foolscap_connection_handlers
    """
    # We store handlers for everything. None means we were unable to
    # create that handler, so hints which want it will be ignored.
    handlers = {
        "tcp": _make_tcp_handler(),
        "tor": tor_provider.get_client_endpoint(),
        "i2p": i2p_provider.get_client_endpoint(),
    }
    log.msg(
        format="built Foolscap connection handlers for: %(known_handlers)s",
        known_handlers=sorted([k for k,v in handlers.items() if v]),
        facility="tahoe.node",
        umid="PuLh8g",
    )
    return create_default_connection_handlers(
        config,
        handlers,
    ), handlers


def create_tub(tub_options, default_connection_handlers, foolscap_connection_handlers,
               handler_overrides=None, force_foolscap=False, **kwargs):
    """
    Create a Tub with the right options and handlers. It will be
    ephemeral unless the caller provides certFile= in kwargs

    :param handler_overrides: anything in this will override anything
        in `default_connection_handlers` for just this call.

    :param dict tub_options: every key-value pair in here will be set in
        the new Tub via `Tub.setOption`

    :param bool force_foolscap: If True, only allow Foolscap, not just HTTPS
        storage protocol.
    """
    if handler_overrides is None:
        handler_overrides = {}
    # We listen simultaneously for both Foolscap and HTTPS on the same port,
    # so we have to create a special Foolscap Tub for that to work:
    if force_foolscap:
        tub = Tub(**kwargs)
    else:
        tub = create_tub_with_https_support(**kwargs)

    for (name, value) in list(tub_options.items()):
        tub.setOption(name, value)
    handlers = default_connection_handlers.copy()
    handlers.update(handler_overrides)
    tub.removeAllConnectionHintHandlers()
    for hint_type, handler_name in list(handlers.items()):
        handler = foolscap_connection_handlers.get(handler_name)
        if handler:
            tub.addConnectionHintHandler(hint_type, handler)
    return tub


def _convert_tub_port(s):
    """
    :returns: a proper Twisted endpoint string like (`tcp:X`) is `s`
        is a bare number, or returns `s` as-is
    """
    us = s
    if isinstance(s, bytes):
        us = s.decode("utf-8")
    if re.search(r'^\d+$', us):
        return "tcp:{}".format(int(us))
    return us


class PortAssignmentRequired(Exception):
    """
    A Tub port number was configured to be 0 where this is not allowed.
    """


def _tub_portlocation(config, get_local_addresses_sync, allocate_tcp_port):
    """
    Figure out the network location of the main tub for some configuration.

    :param get_local_addresses_sync: A function like
        ``iputil.get_local_addresses_sync``.

    :param allocate_tcp_port: A function like ``iputil.allocate_tcp_port``.

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
            tubport = "tcp:%d" % (allocate_tcp_port(),)
            fileutil.write_atomically(config.portnum_fname, tubport + "\n",
                                      mode="")
    else:
        tubport = _convert_tub_port(cfg_tubport)

    for port in tubport.split(","):
        if port in ("0", "tcp:0", "tcp:port=0", "tcp:0:interface=127.0.0.1"):
            raise PortAssignmentRequired()

    if cfg_location is None:
        cfg_location = "AUTO"

    local_portnum = None # needed to hush lgtm.com static analyzer
    # Replace the location "AUTO", if present, with the detected local
    # addresses. Don't probe for local addresses unless necessary.
    split_location = cfg_location.split(",")
    if "AUTO" in split_location:
        if not reveal_ip:
            raise PrivacyError("tub.location uses AUTO")
        local_addresses = get_local_addresses_sync()
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

    # Lacking this, Python 2 blows up in Foolscap when it is confused by a
    # Unicode FURL.
    location = location.encode("utf-8")

    return tubport, location


def tub_listen_on(i2p_provider, tor_provider, tub, tubport, location):
    """
    Assign a Tub its listener locations.

    :param i2p_provider: See ``allmydata.util.i2p_provider.create``.
    :param tor_provider: See ``allmydata.util.tor_provider.create``.
    """
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
        # Foolscap requires native strings:
        if isinstance(port_or_endpoint, (bytes, str)):
            port_or_endpoint = ensure_str(port_or_endpoint)
        tub.listenOn(port_or_endpoint)
    # This last step makes the Tub is ready for tub.registerReference()
    tub.setLocation(location)


def create_main_tub(config, tub_options,
                    default_connection_handlers, foolscap_connection_handlers,
                    i2p_provider, tor_provider,
                    handler_overrides=None, cert_filename="node.pem"):
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
    if handler_overrides is None:
        handler_overrides = {}
    portlocation = _tub_portlocation(
        config,
        iputil.get_local_addresses_sync,
        iputil.allocate_tcp_port,
    )

    # FIXME? "node.pem" was the CERTFILE option/thing
    certfile = config.get_private_path("node.pem")
    tub = create_tub(
        tub_options,
        default_connection_handlers,
        foolscap_connection_handlers,
        force_foolscap=config.get_config(
            "storage", "force_foolscap", default=False, boolean=True
        ),
        handler_overrides=handler_overrides,
        certFile=certfile,
    )

    if portlocation is None:
        log.msg("Tub is not listening")
    else:
        tubport, location = portlocation
        tub_listen_on(
            i2p_provider,
            tor_provider,
            tub,
            tubport,
            location,
        )
        log.msg("Tub location set to %r" % (location,))
    return tub


class Node(service.MultiService):
    """
    This class implements common functionality of both Client nodes and Introducer nodes.
    """
    NODETYPE = "unknown NODETYPE"
    CERTFILE = "node.pem"

    def __init__(self, config, main_tub, i2p_provider, tor_provider):
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

        self.create_log_tub()
        self.logSource = "Node"
        self.setup_logging()

        self.tub = main_tub
        if self.tub is not None:
            self.nodeid = b32decode(self.tub.tubID.upper())  # binary format
            self.short_nodeid = b32encode(self.nodeid).lower()[:8]  # for printing
            self.config.write_config_file("my_nodeid", b32encode(self.nodeid).lower() + b"\n", mode="wb")
            self.tub.setServiceParent(self)
        else:
            self.nodeid = self.short_nodeid = None

        self.log("Node constructed. " + __full_version__)
        iputil.increase_rlimits()

    def _is_tub_listening(self):
        """
        :returns: True if the main tub is listening
        """
        return len(self.tub.getListeners()) > 0

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
                ob = o.__self__
                if isinstance(ob, twlog.FileLogObserver):
                    newmeth = types.MethodType(formatTimeTahoeStyle, ob)
                    ob.formatTime = newmeth
        # TODO: twisted >2.5.0 offers maxRotatedFiles=50

        lgfurl_file = self.config.get_private_path("logport.furl").encode(get_filesystem_encoding())
        if os.path.exists(lgfurl_file):
            os.remove(lgfurl_file)
        self.log_tub.setOption("logport-furlfile", lgfurl_file)
        lgfurl = self.config.get_config("node", "log_gatherer.furl", "")
        if lgfurl:
            # this is in addition to the contents of log-gatherer-furlfile
            lgfurl = lgfurl.encode("utf-8")
            self.log_tub.setOption("log-gatherer-furl", lgfurl)
        self.log_tub.setOption("log-gatherer-furlfile",
                               self.config.get_config_path("log_gatherer.furl"))

        incident_dir = self.config.get_config_path("logs", "incidents")
        foolscap.logging.log.setLogDir(incident_dir)
        twlog.msg("Foolscap logging initialized")
        twlog.msg("Note to developers: twistd.log does not receive very much.")
        twlog.msg("Use 'flogtool tail -c NODEDIR/private/logport.furl' instead")
        twlog.msg("and read docs/logging.rst")

    def log(self, *args, **kwargs):
        return log.msg(*args, **kwargs)
