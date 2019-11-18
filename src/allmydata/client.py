import os, stat, time, weakref
from base64 import urlsafe_b64encode
from functools import partial
from errno import ENOENT, EPERM
from ConfigParser import NoSectionError

from foolscap.furl import (
    decode_furl,
)

import attr
from zope.interface import implementer

from twisted.plugin import (
    getPlugins,
)
from twisted.internet import reactor, defer
from twisted.application import service
from twisted.application.internet import TimerService
from twisted.python.filepath import FilePath

import allmydata
from allmydata.crypto import rsa, ed25519
from allmydata.crypto.util import remove_prefix
from allmydata.storage.server import StorageServer
from allmydata import storage_client
from allmydata.immutable.upload import Uploader
from allmydata.immutable.offloaded import Helper
from allmydata.control import ControlServer
from allmydata.introducer.client import IntroducerClient
from allmydata.util import (
    hashutil, base32, pollmixin, log, idlib,
    yamlutil, configutil,
)
from allmydata.util.encodingutil import (get_filesystem_encoding,
                                         from_utf8_or_none)
from allmydata.util.abbreviate import parse_abbreviated_size
from allmydata.util.time_format import parse_duration, parse_date
from allmydata.util.i2p_provider import create as create_i2p_provider
from allmydata.util.tor_provider import create as create_tor_provider
from allmydata.stats import StatsProvider
from allmydata.history import History
from allmydata.interfaces import (
    IStatsProducer,
    SDMF_VERSION,
    MDMF_VERSION,
    DEFAULT_MAX_SEGMENT_SIZE,
    IFoolscapStoragePlugin,
    IAnnounceableStorageServer,
)
from allmydata.nodemaker import NodeMaker
from allmydata.blacklist import Blacklist
from allmydata import node


KiB=1024
MiB=1024*KiB
GiB=1024*MiB
TiB=1024*GiB
PiB=1024*TiB

def _is_valid_section(section_name):
    """
    Check for valid dynamic configuration section names.

    Currently considers all possible storage server plugin sections valid.
    """
    return (
        section_name.startswith(b"storageserver.plugins.") or
        section_name.startswith(b"storageclient.plugins.")
    )


_client_config = configutil.ValidConfiguration(
    static_valid_sections={
        "client": (
            "helper.furl",
            "introducer.furl",
            "key_generator.furl",
            "mutable.format",
            "peers.preferred",
            "shares.happy",
            "shares.needed",
            "shares.total",
            "stats_gatherer.furl",
            "storage.plugins",
        ),
        "drop_upload": (  # deprecated already?
            "enabled",
        ),
        "ftpd": (
            "accounts.file",
            "accounts.url",
            "enabled",
            "port",
        ),
        "storage": (
            "debug_discard",
            "enabled",
            "anonymous",
            "expire.cutoff_date",
            "expire.enabled",
            "expire.immutable",
            "expire.mode",
            "expire.mode",
            "expire.mutable",
            "expire.override_lease_duration",
            "readonly",
            "reserved_space",
            "storage_dir",
            "plugins",
        ),
        "sftpd": (
            "accounts.file",
            "accounts.url",
            "enabled",
            "host_privkey_file",
            "host_pubkey_file",
            "port",
        ),
        "helper": (
            "enabled",
        ),
        "magic_folder": (
            "download.umask",
            "enabled",
            "local.directory",
            "poll_interval",
        ),
    },
    is_valid_section=_is_valid_section,
    # Anything in a valid section is a valid item, for now.
    is_valid_item=lambda section, ignored: _is_valid_section(section),
)


def _valid_config():
    cfg = node._common_valid_config()
    return cfg.update(_client_config)

# this is put into README in new node-directories
CLIENT_README = """
This directory contains files which contain private data for the Tahoe node,
such as private keys.  On Unix-like systems, the permissions on this directory
are set to disallow users other than its owner from reading the contents of
the files.   See the 'configuration.rst' documentation file for details.
"""



def _make_secret():
    """
    Returns a base32-encoded random secret of hashutil.CRYPTO_VAL_SIZE
    bytes.
    """
    return base32.b2a(os.urandom(hashutil.CRYPTO_VAL_SIZE)) + "\n"


class SecretHolder(object):
    def __init__(self, lease_secret, convergence_secret):
        self._lease_secret = lease_secret
        self._convergence_secret = convergence_secret

    def get_renewal_secret(self):
        return hashutil.my_renewal_secret_hash(self._lease_secret)

    def get_cancel_secret(self):
        return hashutil.my_cancel_secret_hash(self._lease_secret)

    def get_convergence_secret(self):
        return self._convergence_secret

class KeyGenerator(object):
    """I create RSA keys for mutable files. Each call to generate() returns a
    single keypair. The keysize is specified first by the keysize= argument
    to generate(), then with a default set by set_default_keysize(), then
    with a built-in default of 2048 bits."""
    def __init__(self):
        self.default_keysize = 2048

    def set_default_keysize(self, keysize):
        """Call this to override the size of the RSA keys created for new
        mutable files which don't otherwise specify a size. This will affect
        all subsequent calls to generate() without a keysize= argument. The
        default size is 2048 bits. Test cases should call this method once
        during setup, to cause me to create smaller keys, so the unit tests
        run faster."""
        self.default_keysize = keysize

    def generate(self, keysize=None):
        """I return a Deferred that fires with a (verifyingkey, signingkey)
        pair. I accept a keysize in bits (2048 bit keys are standard, smaller
        keys are used for testing). If you do not provide a keysize, I will
        use my default, which is set by a call to set_default_keysize(). If
        set_default_keysize() has never been called, I will create 2048 bit
        keys."""
        keysize = keysize or self.default_keysize
        # RSA key generation for a 2048 bit key takes between 0.8 and 3.2
        # secs
        signer, verifier = rsa.create_signing_keypair(keysize)
        return defer.succeed( (verifier, signer) )

class Terminator(service.Service):
    def __init__(self):
        self._clients = weakref.WeakKeyDictionary()
    def register(self, c):
        self._clients[c] = None
    def stopService(self):
        for c in self._clients:
            c.stop()
        return service.Service.stopService(self)


def read_config(basedir, portnumfile, generated_files=[]):
    """
    Read and validate configuration for a client-style Node. See
    :method:`allmydata.node.read_config` for parameter meanings (the
    only difference here is we pass different validation data)

    :returns: :class:`allmydata.node._Config` instance
    """
    return node.read_config(
        basedir, portnumfile,
        generated_files=generated_files,
        _valid_config=_valid_config(),
    )


config_from_string = partial(
    node.config_from_string,
    _valid_config=_valid_config(),
)


def create_client(basedir=u".", _client_factory=None):
    """
    Creates a new client instance (a subclass of Node).

    :param unicode basedir: the node directory (which may not exist yet)

    :param _client_factory: (for testing) a callable that returns an
        instance of :class:`allmydata.node.Node` (or a subclass). By default
        this is :class:`allmydata.client._Client`

    :returns: Deferred yielding an instance of :class:`allmydata.client._Client`
    """
    try:
        node.create_node_dir(basedir, CLIENT_README)
        config = read_config(basedir, u"client.port")
        # following call is async
        return create_client_from_config(
            config,
            _client_factory=_client_factory,
        )
    except Exception:
        return defer.fail()


@defer.inlineCallbacks
def create_client_from_config(config, _client_factory=None, _introducer_factory=None):
    """
    Creates a new client instance (a subclass of Node).  Most code
    should probably use `create_client` instead.

    :returns: Deferred yielding a _Client instance

    :param config: configuration instance (from read_config()) which
        encapsulates everything in the "node directory".

    :param _client_factory: for testing; the class to instantiate
        instead of _Client

    :param _introducer_factory: for testing; the class to instantiate instead
        of IntroducerClient
    """
    if _client_factory is None:
        _client_factory = _Client

    i2p_provider = create_i2p_provider(reactor, config)
    tor_provider = create_tor_provider(reactor, config)
    handlers = node.create_connection_handlers(reactor, config, i2p_provider, tor_provider)
    default_connection_handlers, foolscap_connection_handlers = handlers
    tub_options = node.create_tub_options(config)

    main_tub = node.create_main_tub(
        config, tub_options, default_connection_handlers,
        foolscap_connection_handlers, i2p_provider, tor_provider,
    )
    control_tub = node.create_control_tub()

    introducer_clients = create_introducer_clients(config, main_tub, _introducer_factory)
    storage_broker = create_storage_farm_broker(
        config, default_connection_handlers, foolscap_connection_handlers,
        tub_options, introducer_clients
    )

    client = _client_factory(
        config,
        main_tub,
        control_tub,
        i2p_provider,
        tor_provider,
        introducer_clients,
        storage_broker,
    )

    # Initialize storage separately after creating the client.  This is
    # necessary because we need to pass a reference to the client in to the
    # storage plugins to allow them to initialize themselves (specifically,
    # they may want the anonymous IStorageServer implementation so they don't
    # have to duplicate all of its basic storage functionality).  A better way
    # to do this, eventually, may be to create that implementation first and
    # then pass it in to both storage plugin creation and the client factory.
    # This avoids making a partially initialized client object escape the
    # client factory and removes the circular dependency between these
    # objects.
    storage_plugins = yield _StoragePlugins.from_config(
        client.get_anonymous_storage_server,
        config,
    )
    client.init_storage(storage_plugins.announceable_storage_servers)

    i2p_provider.setServiceParent(client)
    tor_provider.setServiceParent(client)
    for ic in introducer_clients:
        ic.setServiceParent(client)
    storage_broker.setServiceParent(client)
    defer.returnValue(client)


@attr.s
class _StoragePlugins(object):
    """
    Functionality related to getting storage plugins set up and ready for use.

    :ivar list[IAnnounceableStorageServer] announceable_storage_servers: The
        announceable storage servers that should be used according to node
        configuration.
    """
    announceable_storage_servers = attr.ib()

    @classmethod
    @defer.inlineCallbacks
    def from_config(cls, get_anonymous_storage_server, config):
        """
        Load and configured storage plugins.

        :param get_anonymous_storage_server: A no-argument callable which
            returns the node's anonymous ``IStorageServer`` implementation.

        :param _Config config: The node's configuration.

        :return: A ``_StoragePlugins`` initialized from the given
            configuration.
        """
        storage_plugin_names = cls._get_enabled_storage_plugin_names(config)
        plugins = list(cls._collect_storage_plugins(storage_plugin_names))
        unknown_plugin_names = storage_plugin_names - {plugin.name for plugin in plugins}
        if unknown_plugin_names:
            raise configutil.UnknownConfigError(
                "Storage plugins {} are enabled but not known on this system.".format(
                    unknown_plugin_names,
                ),
            )
        announceable_storage_servers = yield cls._create_plugin_storage_servers(
            get_anonymous_storage_server,
            config,
            plugins,
        )
        defer.returnValue(cls(
            announceable_storage_servers,
        ))

    @classmethod
    def _get_enabled_storage_plugin_names(cls, config):
        """
        Get the names of storage plugins that are enabled in the configuration.
        """
        return set(
            config.get_config(
                "storage", "plugins", b""
            ).decode("ascii").split(u",")
        ) - {u""}

    @classmethod
    def _collect_storage_plugins(cls, storage_plugin_names):
        """
        Get the storage plugins with names matching those given.
        """
        return list(
            plugin
            for plugin
            in getPlugins(IFoolscapStoragePlugin)
            if plugin.name in storage_plugin_names
        )

    @classmethod
    def _create_plugin_storage_servers(cls, get_anonymous_storage_server, config, plugins):
        """
        Cause each storage plugin to instantiate its storage server and return
        them all.

        :return: A ``Deferred`` that fires with storage servers instantiated
            by all of the given storage server plugins.
        """
        return defer.gatherResults(
            list(
                plugin.get_storage_server(
                    cls._get_storage_plugin_configuration(config, plugin.name),
                    get_anonymous_storage_server,
                ).addCallback(
                    partial(
                        _add_to_announcement,
                        {u"name": plugin.name},
                    ),
                )
                for plugin
                # The order is fairly arbitrary and it is not meant to convey
                # anything but providing *some* stable ordering makes the data
                # a little easier to deal with (mainly in tests and when
                # manually inspecting it).
                in sorted(plugins, key=lambda p: p.name)
            ),
        )

    @classmethod
    def _get_storage_plugin_configuration(cls, config, storage_plugin_name):
        """
        Load the configuration for a storage server plugin with the given name.

        :return dict[bytes, bytes]: The matching configuration.
        """
        try:
            config = config.items(
                "storageserver.plugins." + storage_plugin_name,
            )
        except NoSectionError:
            config = []
        return dict(config)



def _sequencer(config):
    """
    :returns: a 2-tuple consisting of a new announcement
        sequence-number and random nonce (int, unicode). Reads and
        re-writes configuration file "announcement-seqnum" (starting at 1
        if that file doesn't exist).
    """
    seqnum_s = config.get_config_from_file("announcement-seqnum")
    if not seqnum_s:
        seqnum_s = u"0"
    seqnum = int(seqnum_s.strip())
    seqnum += 1  # increment
    config.write_config_file("announcement-seqnum", "{}\n".format(seqnum))
    nonce = _make_secret().strip()
    return seqnum, nonce


def create_introducer_clients(config, main_tub, _introducer_factory=None):
    """
    Read, validate and parse any 'introducers.yaml' configuration.

    :param _introducer_factory: for testing; the class to instantiate instead
        of IntroducerClient

    :returns: a list of IntroducerClient instances
    """
    if _introducer_factory is None:
        _introducer_factory = IntroducerClient

    # we return this list
    introducer_clients = []

    introducers_yaml_filename = config.get_private_path("introducers.yaml")
    introducers_filepath = FilePath(introducers_yaml_filename)

    try:
        with introducers_filepath.open() as f:
            introducers_yaml = yamlutil.safe_load(f)
            if introducers_yaml is None:
                raise EnvironmentError(
                    EPERM,
                    "Can't read '{}'".format(introducers_yaml_filename),
                    introducers_yaml_filename,
                )
            introducers = introducers_yaml.get("introducers", {})
            log.msg(
                "found {} introducers in private/introducers.yaml".format(
                    len(introducers),
                )
            )
    except EnvironmentError as e:
        if e.errno != ENOENT:
            raise
        introducers = {}

    if "default" in introducers.keys():
        raise ValueError(
            "'default' introducer furl cannot be specified in introducers.yaml;"
            " please fix impossible configuration."
        )

    # read furl from tahoe.cfg
    tahoe_cfg_introducer_furl = config.get_config("client", "introducer.furl", None)
    if tahoe_cfg_introducer_furl == "None":
        raise ValueError(
            "tahoe.cfg has invalid 'introducer.furl = None':"
            " to disable it, use 'introducer.furl ='"
            " or omit the key entirely"
        )
    if tahoe_cfg_introducer_furl:
        introducers[u'default'] = {'furl':tahoe_cfg_introducer_furl}

    for petname, introducer in introducers.items():
        introducer_cache_filepath = FilePath(config.get_private_path("introducer_{}_cache.yaml".format(petname)))
        ic = _introducer_factory(
            main_tub,
            introducer['furl'].encode("ascii"),
            config.nickname,
            str(allmydata.__full_version__),
            str(_Client.OLDEST_SUPPORTED_VERSION),
            node.get_app_versions(),
            partial(_sequencer, config),
            introducer_cache_filepath,
        )
        introducer_clients.append(ic)
    return introducer_clients


def create_storage_farm_broker(config, default_connection_handlers, foolscap_connection_handlers, tub_options, introducer_clients):
    """
    Create a StorageFarmBroker object, for use by Uploader/Downloader
    (and everybody else who wants to use storage servers)

    :param config: a _Config instance

    :param default_connection_handlers: default Foolscap handlers

    :param foolscap_connection_handlers: available/configured Foolscap
        handlers

    :param dict tub_options: how to configure our Tub

    :param list introducer_clients: IntroducerClient instances if
        we're connecting to any
    """
    storage_client_config = storage_client.StorageClientConfig.from_node_config(
        config,
    )

    def tub_creator(handler_overrides=None, **kwargs):
        return node.create_tub(
            tub_options,
            default_connection_handlers,
            foolscap_connection_handlers,
            handler_overrides={} if handler_overrides is None else handler_overrides,
            **kwargs
        )

    sb = storage_client.StorageFarmBroker(
        permute_peers=True,
        tub_maker=tub_creator,
        node_config=config,
        storage_client_config=storage_client_config,
    )
    for ic in introducer_clients:
        sb.use_introducer(ic)
    return sb


def _register_reference(key, config, tub, referenceable):
    """
    Register a referenceable in a tub with a stable fURL.

    Stability is achieved by storing the fURL in the configuration the first
    time and then reading it back on for future calls.

    :param bytes key: An identifier for this reference which can be used to
        identify its fURL in the configuration.

    :param _Config config: The configuration to use for fURL persistence.

    :param Tub tub: The tub in which to register the reference.

    :param Referenceable referenceable: The referenceable to register in the
        Tub.

    :return bytes: The fURL at which the object is registered.
    """
    persisted_furl = config.get_private_config(
        key,
        default=None,
    )
    name = None
    if persisted_furl is not None:
        _, _, name = decode_furl(persisted_furl)
    registered_furl = tub.registerReference(
        referenceable,
        name=name,
    )
    if persisted_furl is None:
        config.write_private_config(key, registered_furl)
    return registered_furl


@implementer(IAnnounceableStorageServer)
@attr.s
class AnnounceableStorageServer(object):
    announcement = attr.ib()
    storage_server = attr.ib()



def _add_to_announcement(information, announceable_storage_server):
    """
    Create a new ``AnnounceableStorageServer`` based on
    ``announceable_storage_server`` with ``information`` added to its
    ``announcement``.
    """
    updated_announcement = announceable_storage_server.announcement.copy()
    updated_announcement.update(information)
    return AnnounceableStorageServer(
        updated_announcement,
        announceable_storage_server.storage_server,
    )


def storage_enabled(config):
    """
    Is storage enabled according to the given configuration object?

    :param _Config config: The configuration to inspect.

    :return bool: ``True`` if storage is enabled, ``False`` otherwise.
    """
    return config.get_config(b"storage", b"enabled", True, boolean=True)


def anonymous_storage_enabled(config):
    """
    Is anonymous access to storage enabled according to the given
    configuration object?

    :param _Config config: The configuration to inspect.

    :return bool: ``True`` if storage is enabled, ``False`` otherwise.
    """
    return (
        storage_enabled(config) and
        config.get_config(b"storage", b"anonymous", True, boolean=True)
    )


@implementer(IStatsProducer)
class _Client(node.Node, pollmixin.PollMixin):

    STOREDIR = 'storage'
    NODETYPE = "client"
    EXIT_TRIGGER_FILE = "exit_trigger"

    # This means that if a storage server treats me as though I were a
    # 1.0.0 storage client, it will work as they expect.
    OLDEST_SUPPORTED_VERSION = "1.0.0"

    # This is a dictionary of (needed, desired, total, max_segment_size). 'needed'
    # is the number of shares required to reconstruct a file. 'desired' means
    # that we will abort an upload unless we can allocate space for at least
    # this many. 'total' is the total number of shares created by encoding.
    # If everybody has room then this is is how many we will upload.
    DEFAULT_ENCODING_PARAMETERS = {"k": 3,
                                   "happy": 7,
                                   "n": 10,
                                   "max_segment_size": DEFAULT_MAX_SEGMENT_SIZE,
                                   }

    def __init__(self, config, main_tub, control_tub, i2p_provider, tor_provider, introducer_clients,
                 storage_farm_broker):
        """
        Use :func:`allmydata.client.create_client` to instantiate one of these.
        """
        node.Node.__init__(self, config, main_tub, control_tub, i2p_provider, tor_provider)

        self._magic_folders = dict()
        self.started_timestamp = time.time()
        self.logSource = "Client"
        self.encoding_params = self.DEFAULT_ENCODING_PARAMETERS.copy()

        self.introducer_clients = introducer_clients
        self.storage_broker = storage_farm_broker

        self.init_stats_provider()
        self.init_secrets()
        self.init_node_key()
        self.init_control()
        self._key_generator = KeyGenerator()
        key_gen_furl = config.get_config("client", "key_generator.furl", None)
        if key_gen_furl:
            log.msg("[client]key_generator.furl= is now ignored, see #2783")
        self.init_client()
        self.load_static_servers()
        self.helper = None
        if config.get_config("helper", "enabled", False, boolean=True):
            if not self._is_tub_listening():
                raise ValueError("config error: helper is enabled, but tub "
                                 "is not listening ('tub.port=' is empty)")
            self.init_helper()
        self.init_ftp_server()
        self.init_sftp_server()
        self.init_magic_folder()

        # If the node sees an exit_trigger file, it will poll every second to see
        # whether the file still exists, and what its mtime is. If the file does not
        # exist or has not been modified for a given timeout, the node will exit.
        exit_trigger_file = config.get_config_path(self.EXIT_TRIGGER_FILE)
        if os.path.exists(exit_trigger_file):
            age = time.time() - os.stat(exit_trigger_file)[stat.ST_MTIME]
            self.log("%s file noticed (%ds old), starting timer" % (self.EXIT_TRIGGER_FILE, age))
            exit_trigger = TimerService(1.0, self._check_exit_trigger, exit_trigger_file)
            exit_trigger.setServiceParent(self)

        # this needs to happen last, so it can use getServiceNamed() to
        # acquire references to StorageServer and other web-statusable things
        webport = config.get_config("node", "web.port", None)
        if webport:
            self.init_web(webport) # strports string

    def init_stats_provider(self):
        gatherer_furl = self.config.get_config("client", "stats_gatherer.furl", None)
        self.stats_provider = StatsProvider(self, gatherer_furl)
        self.stats_provider.setServiceParent(self)
        self.stats_provider.register_producer(self)

    def get_stats(self):
        return { 'node.uptime': time.time() - self.started_timestamp }

    def init_secrets(self):
        lease_s = self.config.get_or_create_private_config("secret", _make_secret)
        lease_secret = base32.a2b(lease_s)
        convergence_s = self.config.get_or_create_private_config('convergence',
                                                                 _make_secret)
        self.convergence = base32.a2b(convergence_s)
        self._secret_holder = SecretHolder(lease_secret, self.convergence)

    def init_node_key(self):
        # we only create the key once. On all subsequent runs, we re-use the
        # existing key
        def _make_key():
            private_key, _ = ed25519.create_signing_keypair()
            return ed25519.string_from_signing_key(private_key) + "\n"

        private_key_str = self.config.get_or_create_private_config("node.privkey", _make_key)
        private_key, public_key = ed25519.signing_keypair_from_string(private_key_str)
        public_key_str = ed25519.string_from_verifying_key(public_key)
        self.config.write_config_file("node.pubkey", public_key_str + "\n")
        self._node_private_key = private_key
        self._node_public_key = public_key

    def get_long_nodeid(self):
        # this matches what IServer.get_longname() says about us elsewhere
        vk_string = ed25519.string_from_verifying_key(self._node_public_key)
        return remove_prefix(vk_string, "pub-")

    def get_long_tubid(self):
        return idlib.nodeid_b2a(self.nodeid)

    def get_web_service(self):
        """
        :return: a reference to our web server
        """
        return self.getServiceNamed("webish")

    def _init_permutation_seed(self, ss):
        seed = self.config.get_config_from_file("permutation-seed")
        if not seed:
            have_shares = ss.have_shares()
            if have_shares:
                # if the server has shares but not a recorded
                # permutation-seed, then it has been around since pre-#466
                # days, and the clients who uploaded those shares used our
                # TubID as a permutation-seed. We should keep using that same
                # seed to keep the shares in the same place in the permuted
                # ring, so those clients don't have to perform excessive
                # searches.
                seed = base32.b2a(self.nodeid)
            else:
                # otherwise, we're free to use the more natural seed of our
                # pubkey-based serverid
                vk_string = ed25519.string_from_verifying_key(self._node_public_key)
                vk_bytes = remove_prefix(vk_string, ed25519.PUBLIC_KEY_PREFIX)
                seed = base32.b2a(vk_bytes)
            self.config.write_config_file("permutation-seed", seed+"\n")
        return seed.strip()

    def get_anonymous_storage_server(self):
        """
        Get the anonymous ``IStorageServer`` implementation for this node.

        Note this will return an object even if storage is disabled on this
        node (but the object will not be exposed, peers will not be able to
        access it, and storage will remain disabled).

        The one and only instance for this node is always returned.  It is
        created first if necessary.
        """
        try:
            ss = self.getServiceNamed(StorageServer.name)
        except KeyError:
            pass
        else:
            return ss

        readonly = self.config.get_config("storage", "readonly", False, boolean=True)

        config_storedir = self.get_config(
            "storage", "storage_dir", self.STOREDIR,
        ).decode('utf-8')
        storedir = self.config.get_config_path(config_storedir)

        data = self.config.get_config("storage", "reserved_space", None)
        try:
            reserved = parse_abbreviated_size(data)
        except ValueError:
            log.msg("[storage]reserved_space= contains unparseable value %s"
                    % data)
            raise
        if reserved is None:
            reserved = 0
        discard = self.config.get_config("storage", "debug_discard", False,
                                         boolean=True)

        expire = self.config.get_config("storage", "expire.enabled", False, boolean=True)
        if expire:
            mode = self.config.get_config("storage", "expire.mode") # require a mode
        else:
            mode = self.config.get_config("storage", "expire.mode", "age")

        o_l_d = self.config.get_config("storage", "expire.override_lease_duration", None)
        if o_l_d is not None:
            o_l_d = parse_duration(o_l_d)

        cutoff_date = None
        if mode == "cutoff-date":
            cutoff_date = self.config.get_config("storage", "expire.cutoff_date")
            cutoff_date = parse_date(cutoff_date)

        sharetypes = []
        if self.config.get_config("storage", "expire.immutable", True, boolean=True):
            sharetypes.append("immutable")
        if self.config.get_config("storage", "expire.mutable", True, boolean=True):
            sharetypes.append("mutable")
        expiration_sharetypes = tuple(sharetypes)

        ss = StorageServer(storedir, self.nodeid,
                           reserved_space=reserved,
                           discard_storage=discard,
                           readonly_storage=readonly,
                           stats_provider=self.stats_provider,
                           expiration_enabled=expire,
                           expiration_mode=mode,
                           expiration_override_lease_duration=o_l_d,
                           expiration_cutoff_date=cutoff_date,
                           expiration_sharetypes=expiration_sharetypes)
        ss.setServiceParent(self)
        return ss

    def init_storage(self, announceable_storage_servers):
        # should we run a storage server (and publish it for others to use)?
        if not storage_enabled(self.config):
            return
        if not self._is_tub_listening():
            raise ValueError("config error: storage is enabled, but tub "
                             "is not listening ('tub.port=' is empty)")

        ss = self.get_anonymous_storage_server()
        announcement = {
            "permutation-seed-base32": self._init_permutation_seed(ss),
        }

        if anonymous_storage_enabled(self.config):
            furl_file = self.config.get_private_path("storage.furl").encode(get_filesystem_encoding())
            furl = self.tub.registerReference(ss, furlFile=furl_file)
            announcement["anonymous-storage-FURL"] = furl

        enabled_storage_servers = self._enable_storage_servers(
            announceable_storage_servers,
        )
        storage_options = list(
            storage_server.announcement
            for storage_server
            in enabled_storage_servers
        )
        plugins_announcement = {}
        if storage_options:
            # Only add the new key if there are any plugins enabled.
            plugins_announcement[u"storage-options"] = storage_options

        announcement.update(plugins_announcement)

        for ic in self.introducer_clients:
            ic.publish("storage", announcement, self._node_private_key)

    def get_client_storage_plugin_web_resources(self):
        """
        Get all of the client-side ``IResource`` implementations provided by
        enabled storage plugins.

        :return dict[bytes, IResource provider]: The implementations.
        """
        return self.storage_broker.get_client_storage_plugin_web_resources(
            self.config,
        )

    def _enable_storage_servers(self, announceable_storage_servers):
        """
        Register and announce the given storage servers.
        """
        for announceable in announceable_storage_servers:
            yield self._enable_storage_server(announceable)

    def _enable_storage_server(self, announceable_storage_server):
        """
        Register a storage server.
        """
        config_key = b"storage-plugin.{}.furl".format(
            # Oops, why don't I have a better handle on this value?
            announceable_storage_server.announcement[u"name"],
        )
        furl = _register_reference(
            config_key,
            self.config,
            self.tub,
            announceable_storage_server.storage_server,
        )
        announceable_storage_server = _add_to_announcement(
            {u"storage-server-FURL": furl},
            announceable_storage_server,
        )
        return announceable_storage_server

    def init_client(self):
        helper_furl = self.config.get_config("client", "helper.furl", None)
        if helper_furl in ("None", ""):
            helper_furl = None

        DEP = self.encoding_params
        DEP["k"] = int(self.config.get_config("client", "shares.needed", DEP["k"]))
        DEP["n"] = int(self.config.get_config("client", "shares.total", DEP["n"]))
        DEP["happy"] = int(self.config.get_config("client", "shares.happy", DEP["happy"]))

        # for the CLI to authenticate to local JSON endpoints
        self._create_auth_token()

        self.history = History(self.stats_provider)
        self.terminator = Terminator()
        self.terminator.setServiceParent(self)
        uploader = Uploader(
            helper_furl,
            self.stats_provider,
            self.history,
        )
        uploader.setServiceParent(self)
        self.init_blacklist()
        self.init_nodemaker()

    def get_auth_token(self):
        """
        This returns a local authentication token, which is just some
        random data in "api_auth_token" which must be echoed to API
        calls.

        Currently only the URI '/magic' for magic-folder status; other
        endpoints are invited to include this as well, as appropriate.
        """
        return self.config.get_private_config('api_auth_token')

    def _create_auth_token(self):
        """
        Creates new auth-token data written to 'private/api_auth_token'.

        This is intentionally re-created every time the node starts.
        """
        self.config.write_private_config(
            'api_auth_token',
            urlsafe_b64encode(os.urandom(32)) + '\n',
        )

    def get_storage_broker(self):
        return self.storage_broker

    def load_static_servers(self):
        """
        Load the servers.yaml file if it exists, and provide the static
        server data to the StorageFarmBroker.
        """
        fn = self.config.get_private_path("servers.yaml")
        servers_filepath = FilePath(fn)
        try:
            with servers_filepath.open() as f:
                servers_yaml = yamlutil.safe_load(f)
            static_servers = servers_yaml.get("storage", {})
            log.msg("found %d static servers in private/servers.yaml" %
                    len(static_servers))
            self.storage_broker.set_static_servers(static_servers)
        except EnvironmentError:
            pass

    def init_blacklist(self):
        fn = self.config.get_config_path("access.blacklist")
        self.blacklist = Blacklist(fn)

    def init_nodemaker(self):
        default = self.config.get_config("client", "mutable.format", default="SDMF")
        if default.upper() == "MDMF":
            self.mutable_file_default = MDMF_VERSION
        else:
            self.mutable_file_default = SDMF_VERSION
        self.nodemaker = NodeMaker(self.storage_broker,
                                   self._secret_holder,
                                   self.get_history(),
                                   self.getServiceNamed("uploader"),
                                   self.terminator,
                                   self.get_encoding_parameters(),
                                   self.mutable_file_default,
                                   self._key_generator,
                                   self.blacklist)

    def get_history(self):
        return self.history

    def init_control(self):
        c = ControlServer()
        c.setServiceParent(self)
        control_url = self.control_tub.registerReference(c)
        self.config.write_private_config("control.furl", control_url + "\n")

    def init_helper(self):
        self.helper = Helper(self.config.get_config_path("helper"),
                             self.storage_broker, self._secret_holder,
                             self.stats_provider, self.history)
        # TODO: this is confusing. BASEDIR/private/helper.furl is created by
        # the helper. BASEDIR/helper.furl is consumed by the client who wants
        # to use the helper. I like having the filename be the same, since
        # that makes 'cp' work smoothly, but the difference between config
        # inputs and generated outputs is hard to see.
        helper_furlfile = self.config.get_private_path("helper.furl").encode(get_filesystem_encoding())
        self.tub.registerReference(self.helper, furlFile=helper_furlfile)

    def set_default_mutable_keysize(self, keysize):
        self._key_generator.set_default_keysize(keysize)

    def init_web(self, webport):
        self.log("init_web(webport=%s)", args=(webport,))

        from allmydata.webish import WebishServer
        nodeurl_path = self.config.get_config_path("node.url")
        staticdir_config = self.config.get_config("node", "web.static", "public_html").decode("utf-8")
        staticdir = self.config.get_config_path(staticdir_config)
        ws = WebishServer(self, webport, nodeurl_path, staticdir)
        ws.setServiceParent(self)

    def init_ftp_server(self):
        if self.config.get_config("ftpd", "enabled", False, boolean=True):
            accountfile = from_utf8_or_none(
                self.config.get_config("ftpd", "accounts.file", None))
            if accountfile:
                accountfile = self.config.get_config_path(accountfile)
            accounturl = self.config.get_config("ftpd", "accounts.url", None)
            ftp_portstr = self.config.get_config("ftpd", "port", "8021")

            from allmydata.frontends import ftpd
            s = ftpd.FTPServer(self, accountfile, accounturl, ftp_portstr)
            s.setServiceParent(self)

    def init_sftp_server(self):
        if self.config.get_config("sftpd", "enabled", False, boolean=True):
            accountfile = from_utf8_or_none(
                self.config.get_config("sftpd", "accounts.file", None))
            if accountfile:
                accountfile = self.config.get_config_path(accountfile)
            accounturl = self.config.get_config("sftpd", "accounts.url", None)
            sftp_portstr = self.config.get_config("sftpd", "port", "8022")
            pubkey_file = from_utf8_or_none(self.config.get_config("sftpd", "host_pubkey_file"))
            privkey_file = from_utf8_or_none(self.config.get_config("sftpd", "host_privkey_file"))

            from allmydata.frontends import sftpd
            s = sftpd.SFTPServer(self, accountfile, accounturl,
                                 sftp_portstr, pubkey_file, privkey_file)
            s.setServiceParent(self)

    def init_magic_folder(self):
        #print "init_magic_folder"
        if self.config.get_config("drop_upload", "enabled", False, boolean=True):
            raise node.OldConfigOptionError(
                "The [drop_upload] section must be renamed to [magic_folder].\n"
                "See docs/frontends/magic-folder.rst for more information."
            )

        if self.config.get_config("magic_folder", "enabled", False, boolean=True):
            from allmydata.frontends import magic_folder

            try:
                magic_folders = magic_folder.load_magic_folders(self.config._basedir)
            except Exception as e:
                log.msg("Error loading magic-folder config: {}".format(e))
                raise

            # start processing the upload queue when we've connected to
            # enough servers
            threshold = min(self.encoding_params["k"],
                            self.encoding_params["happy"] + 1)

            for (name, mf_config) in magic_folders.items():
                self.log("Starting magic_folder '{}'".format(name))
                s = magic_folder.MagicFolder.from_config(self, name, mf_config)
                self._magic_folders[name] = s
                s.setServiceParent(self)

                connected_d = self.storage_broker.when_connected_enough(threshold)
                def connected_enough(ign, mf):
                    mf.ready()  # returns a Deferred we ignore
                    return None
                connected_d.addCallback(connected_enough, s)

    def _check_exit_trigger(self, exit_trigger_file):
        if os.path.exists(exit_trigger_file):
            mtime = os.stat(exit_trigger_file)[stat.ST_MTIME]
            if mtime > time.time() - 120.0:
                return
            else:
                self.log("%s file too old, shutting down" % (self.EXIT_TRIGGER_FILE,))
        else:
            self.log("%s file missing, shutting down" % (self.EXIT_TRIGGER_FILE,))
        reactor.stop()

    def get_encoding_parameters(self):
        return self.encoding_params

    def introducer_connection_statuses(self):
        return [ic.connection_status() for ic in self.introducer_clients]

    def connected_to_introducer(self):
        return any([ic.connected_to_introducer() for ic in self.introducer_clients])

    def get_renewal_secret(self): # this will go away
        return self._secret_holder.get_renewal_secret()

    def get_cancel_secret(self):
        return self._secret_holder.get_cancel_secret()

    def debug_wait_for_client_connections(self, num_clients):
        """Return a Deferred that fires (with None) when we have connections
        to the given number of peers. Useful for tests that set up a
        temporary test network and need to know when it is safe to proceed
        with an upload or download."""
        def _check():
            return len(self.storage_broker.get_connected_servers()) >= num_clients
        d = self.poll(_check, 0.5)
        d.addCallback(lambda res: None)
        return d


    # these four methods are the primitives for creating filenodes and
    # dirnodes. The first takes a URI and produces a filenode or (new-style)
    # dirnode. The other three create brand-new filenodes/dirnodes.

    def create_node_from_uri(self, write_uri, read_uri=None, deep_immutable=False, name="<unknown name>"):
        # This returns synchronously.
        # Note that it does *not* validate the write_uri and read_uri; instead we
        # may get an opaque node if there were any problems.
        return self.nodemaker.create_from_cap(write_uri, read_uri, deep_immutable=deep_immutable, name=name)

    def create_dirnode(self, initial_children={}, version=None):
        d = self.nodemaker.create_new_mutable_directory(initial_children, version=version)
        return d

    def create_immutable_dirnode(self, children, convergence=None):
        return self.nodemaker.create_immutable_directory(children, convergence)

    def create_mutable_file(self, contents=None, keysize=None, version=None):
        return self.nodemaker.create_mutable_file(contents, keysize,
                                                  version=version)

    def upload(self, uploadable, reactor=None):
        uploader = self.getServiceNamed("uploader")
        return uploader.upload(uploadable, reactor=reactor)
