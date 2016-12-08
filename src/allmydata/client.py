import os, stat, time, weakref
from allmydata import node
from base64 import urlsafe_b64encode

from zope.interface import implements
from twisted.internet import reactor, defer
from twisted.application import service
from twisted.application.internet import TimerService
from twisted.python.filepath import FilePath
from pycryptopp.publickey import rsa

import allmydata
from allmydata.storage.server import StorageServer
from allmydata import storage_client
from allmydata.immutable.upload import Uploader
from allmydata.immutable.offloaded import Helper
from allmydata.control import ControlServer
from allmydata.introducer.client import IntroducerClient
from allmydata.util import (hashutil, base32, pollmixin, log, keyutil, idlib,
                            yamlutil, configutil)
from allmydata.util.encodingutil import (get_filesystem_encoding,
                                         from_utf8_or_none)
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.util.abbreviate import parse_abbreviated_size
from allmydata.util.time_format import parse_duration, parse_date
from allmydata.stats import StatsProvider
from allmydata.history import History
from allmydata.interfaces import IStatsProducer, SDMF_VERSION, MDMF_VERSION
from allmydata.nodemaker import NodeMaker
from allmydata.blacklist import Blacklist
from allmydata.node import OldConfigOptionError, _common_config_sections


KiB=1024
MiB=1024*KiB
GiB=1024*MiB
TiB=1024*GiB
PiB=1024*TiB

def _valid_config_sections():
    cfg = _common_config_sections()
    cfg.update({
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
            "expire.cutoff_date",
            "expire.enabled",
            "expire.immutable",
            "expire.mode",
            "expire.mode",
            "expire.mutable",
            "expire.override_lease_duration",
            "readonly",
            "reserved_space",
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
    })
    return cfg


def _make_secret():
    return base32.b2a(os.urandom(hashutil.CRYPTO_VAL_SIZE)) + "\n"

class SecretHolder:
    def __init__(self, lease_secret, convergence_secret):
        self._lease_secret = lease_secret
        self._convergence_secret = convergence_secret

    def get_renewal_secret(self):
        return hashutil.my_renewal_secret_hash(self._lease_secret)

    def get_cancel_secret(self):
        return hashutil.my_cancel_secret_hash(self._lease_secret)

    def get_convergence_secret(self):
        return self._convergence_secret

class KeyGenerator:
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
        signer = rsa.generate(keysize)
        verifier = signer.get_verifying_key()
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


class Client(node.Node, pollmixin.PollMixin):
    implements(IStatsProducer)

    PORTNUMFILE = "client.port"
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
                                   "max_segment_size": 128*KiB,
                                   }

    def __init__(self, basedir="."):
        node.Node.__init__(self, basedir)
        # All tub.registerReference must happen *after* we upcall, since
        # that's what does tub.setLocation()
        configutil.validate_config(self.config_fname, self.config,
                                   _valid_config_sections())
        self._magic_folder = None
        self.started_timestamp = time.time()
        self.logSource="Client"
        self.encoding_params = self.DEFAULT_ENCODING_PARAMETERS.copy()
        self.init_introducer_clients()
        self.init_stats_provider()
        self.init_secrets()
        self.init_node_key()
        self.init_storage()
        self.init_control()
        self._key_generator = KeyGenerator()
        key_gen_furl = self.get_config("client", "key_generator.furl", None)
        if key_gen_furl:
            log.msg("[client]key_generator.furl= is now ignored, see #2783")
        self.init_client()
        self.load_static_servers()
        self.helper = None
        if self.get_config("helper", "enabled", False, boolean=True):
            if not self._tub_is_listening:
                raise ValueError("config error: helper is enabled, but tub "
                                 "is not listening ('tub.port=' is empty)")
            self.init_helper()
        self.init_ftp_server()
        self.init_sftp_server()
        self.init_magic_folder()

        # If the node sees an exit_trigger file, it will poll every second to see
        # whether the file still exists, and what its mtime is. If the file does not
        # exist or has not been modified for a given timeout, the node will exit.
        exit_trigger_file = os.path.join(self.basedir,
                                         self.EXIT_TRIGGER_FILE)
        if os.path.exists(exit_trigger_file):
            age = time.time() - os.stat(exit_trigger_file)[stat.ST_MTIME]
            self.log("%s file noticed (%ds old), starting timer" % (self.EXIT_TRIGGER_FILE, age))
            exit_trigger = TimerService(1.0, self._check_exit_trigger, exit_trigger_file)
            exit_trigger.setServiceParent(self)

        # this needs to happen last, so it can use getServiceNamed() to
        # acquire references to StorageServer and other web-statusable things
        webport = self.get_config("node", "web.port", None)
        if webport:
            self.init_web(webport) # strports string

    def _sequencer(self):
        seqnum_s = self.get_config_from_file("announcement-seqnum")
        if not seqnum_s:
            seqnum_s = "0"
        seqnum = int(seqnum_s.strip())
        seqnum += 1 # increment
        self.write_config("announcement-seqnum", "%d\n" % seqnum)
        nonce = _make_secret().strip()
        return seqnum, nonce

    def init_introducer_clients(self):
        self.introducer_clients = []
        self.introducer_furls = []

        introducers_yaml_filename = os.path.join(self.basedir, "private", "introducers.yaml")
        introducers_filepath = FilePath(introducers_yaml_filename)

        try:
            with introducers_filepath.open() as f:
                introducers_yaml = yamlutil.safe_load(f)
                introducers = introducers_yaml.get("introducers", {})
                log.msg("found %d introducers in private/introducers.yaml" %
                        len(introducers))
        except EnvironmentError:
            introducers = {}

        if "default" in introducers.keys():
            raise ValueError("'default' introducer furl cannot be specified in introducers.yaml; please fix impossible configuration.")

        # read furl from tahoe.cfg
        tahoe_cfg_introducer_furl = self.get_config("client", "introducer.furl", None)
        if tahoe_cfg_introducer_furl == "None":
            raise ValueError("tahoe.cfg has invalid 'introducer.furl = None':"
                             " to disable it, use 'introducer.furl ='"
                             " or omit the key entirely")
        if tahoe_cfg_introducer_furl:
            introducers[u'default'] = {'furl':tahoe_cfg_introducer_furl}

        for petname, introducer in introducers.items():
            introducer_cache_filepath = FilePath(os.path.join(self.basedir, "private", "introducer_{}_cache.yaml".format(petname)))
            ic = IntroducerClient(self.tub, introducer['furl'],
                                  self.nickname,
                                  str(allmydata.__full_version__),
                                  str(self.OLDEST_SUPPORTED_VERSION),
                                  self.get_app_versions(), self._sequencer, introducer_cache_filepath)
            self.introducer_clients.append(ic)
            self.introducer_furls.append(introducer['furl'])
            ic.setServiceParent(self)

    def init_stats_provider(self):
        gatherer_furl = self.get_config("client", "stats_gatherer.furl", None)
        self.stats_provider = StatsProvider(self, gatherer_furl)
        self.add_service(self.stats_provider)
        self.stats_provider.register_producer(self)

    def get_stats(self):
        return { 'node.uptime': time.time() - self.started_timestamp }

    def init_secrets(self):
        lease_s = self.get_or_create_private_config("secret", _make_secret)
        lease_secret = base32.a2b(lease_s)
        convergence_s = self.get_or_create_private_config('convergence',
                                                          _make_secret)
        self.convergence = base32.a2b(convergence_s)
        self._secret_holder = SecretHolder(lease_secret, self.convergence)

    def init_node_key(self):
        # we only create the key once. On all subsequent runs, we re-use the
        # existing key
        def _make_key():
            sk_vs,vk_vs = keyutil.make_keypair()
            return sk_vs+"\n"
        sk_vs = self.get_or_create_private_config("node.privkey", _make_key)
        sk,vk_vs = keyutil.parse_privkey(sk_vs.strip())
        self.write_config("node.pubkey", vk_vs+"\n")
        self._node_key = sk

    def get_long_nodeid(self):
        # this matches what IServer.get_longname() says about us elsewhere
        vk_bytes = self._node_key.get_verifying_key_bytes()
        return "v0-"+base32.b2a(vk_bytes)

    def get_long_tubid(self):
        return idlib.nodeid_b2a(self.nodeid)

    def _init_permutation_seed(self, ss):
        seed = self.get_config_from_file("permutation-seed")
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
                vk_bytes = self._node_key.get_verifying_key_bytes()
                seed = base32.b2a(vk_bytes)
            self.write_config("permutation-seed", seed+"\n")
        return seed.strip()

    def init_storage(self):
        # should we run a storage server (and publish it for others to use)?
        if not self.get_config("storage", "enabled", True, boolean=True):
            return
        if not self._tub_is_listening:
            raise ValueError("config error: storage is enabled, but tub "
                             "is not listening ('tub.port=' is empty)")
        readonly = self.get_config("storage", "readonly", False, boolean=True)

        storedir = os.path.join(self.basedir, self.STOREDIR)

        data = self.get_config("storage", "reserved_space", None)
        try:
            reserved = parse_abbreviated_size(data)
        except ValueError:
            log.msg("[storage]reserved_space= contains unparseable value %s"
                    % data)
            raise
        if reserved is None:
            reserved = 0
        discard = self.get_config("storage", "debug_discard", False,
                                  boolean=True)

        expire = self.get_config("storage", "expire.enabled", False, boolean=True)
        if expire:
            mode = self.get_config("storage", "expire.mode") # require a mode
        else:
            mode = self.get_config("storage", "expire.mode", "age")

        o_l_d = self.get_config("storage", "expire.override_lease_duration", None)
        if o_l_d is not None:
            o_l_d = parse_duration(o_l_d)

        cutoff_date = None
        if mode == "cutoff-date":
            cutoff_date = self.get_config("storage", "expire.cutoff_date")
            cutoff_date = parse_date(cutoff_date)

        sharetypes = []
        if self.get_config("storage", "expire.immutable", True, boolean=True):
            sharetypes.append("immutable")
        if self.get_config("storage", "expire.mutable", True, boolean=True):
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
        self.add_service(ss)

        furl_file = os.path.join(self.basedir, "private", "storage.furl").encode(get_filesystem_encoding())
        furl = self.tub.registerReference(ss, furlFile=furl_file)
        ann = {"anonymous-storage-FURL": furl,
               "permutation-seed-base32": self._init_permutation_seed(ss),
               }
        for ic in self.introducer_clients:
            ic.publish("storage", ann, self._node_key)

    def init_client(self):
        helper_furl = self.get_config("client", "helper.furl", None)
        if helper_furl in ("None", ""):
            helper_furl = None

        DEP = self.encoding_params
        DEP["k"] = int(self.get_config("client", "shares.needed", DEP["k"]))
        DEP["n"] = int(self.get_config("client", "shares.total", DEP["n"]))
        DEP["happy"] = int(self.get_config("client", "shares.happy", DEP["happy"]))

        # for the CLI to authenticate to local JSON endpoints
        self._create_auth_token()

        self.init_client_storage_broker()
        self.history = History(self.stats_provider)
        self.terminator = Terminator()
        self.terminator.setServiceParent(self)
        self.add_service(Uploader(helper_furl, self.stats_provider,
                                  self.history))
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
        return self.get_private_config('api_auth_token')

    def _create_auth_token(self):
        """
        Creates new auth-token data written to 'private/api_auth_token'.

        This is intentionally re-created every time the node starts.
        """
        self.write_private_config(
            'api_auth_token',
            urlsafe_b64encode(os.urandom(32)) + '\n',
        )

    def init_client_storage_broker(self):
        # create a StorageFarmBroker object, for use by Uploader/Downloader
        # (and everybody else who wants to use storage servers)
        ps = self.get_config("client", "peers.preferred", "").split(",")
        preferred_peers = tuple([p.strip() for p in ps if p != ""])
        sb = storage_client.StorageFarmBroker(permute_peers=True,
                                              tub_maker=self._create_tub,
                                              preferred_peers=preferred_peers,
                                              )
        self.storage_broker = sb
        sb.setServiceParent(self)
        for ic in self.introducer_clients:
            sb.use_introducer(ic)

    def get_storage_broker(self):
        return self.storage_broker

    def load_static_servers(self):
        """
        Load the servers.yaml file if it exists, and provide the static
        server data to the StorageFarmBroker.
        """
        fn = os.path.join(self.basedir, "private", "servers.yaml")
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
        fn = os.path.join(self.basedir, "access.blacklist")
        self.blacklist = Blacklist(fn)

    def init_nodemaker(self):
        default = self.get_config("client", "mutable.format", default="SDMF")
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
        self.write_private_config("control.furl", control_url + "\n")

    def init_helper(self):
        self.helper = Helper(os.path.join(self.basedir, "helper"),
                             self.storage_broker, self._secret_holder,
                             self.stats_provider, self.history)
        # TODO: this is confusing. BASEDIR/private/helper.furl is created by
        # the helper. BASEDIR/helper.furl is consumed by the client who wants
        # to use the helper. I like having the filename be the same, since
        # that makes 'cp' work smoothly, but the difference between config
        # inputs and generated outputs is hard to see.
        helper_furlfile = os.path.join(self.basedir,
                                       "private", "helper.furl").encode(get_filesystem_encoding())
        self.tub.registerReference(self.helper, furlFile=helper_furlfile)

    def set_default_mutable_keysize(self, keysize):
        self._key_generator.set_default_keysize(keysize)

    def init_web(self, webport):
        self.log("init_web(webport=%s)", args=(webport,))

        from allmydata.webish import WebishServer
        nodeurl_path = os.path.join(self.basedir, "node.url")
        staticdir_config = self.get_config("node", "web.static", "public_html").decode("utf-8")
        staticdir = abspath_expanduser_unicode(staticdir_config, base=self.basedir)
        ws = WebishServer(self, webport, nodeurl_path, staticdir)
        self.add_service(ws)

    def init_ftp_server(self):
        if self.get_config("ftpd", "enabled", False, boolean=True):
            accountfile = from_utf8_or_none(
                self.get_config("ftpd", "accounts.file", None))
            if accountfile:
                accountfile = abspath_expanduser_unicode(accountfile, base=self.basedir)
            accounturl = self.get_config("ftpd", "accounts.url", None)
            ftp_portstr = self.get_config("ftpd", "port", "8021")

            from allmydata.frontends import ftpd
            s = ftpd.FTPServer(self, accountfile, accounturl, ftp_portstr)
            s.setServiceParent(self)

    def init_sftp_server(self):
        if self.get_config("sftpd", "enabled", False, boolean=True):
            accountfile = from_utf8_or_none(
                self.get_config("sftpd", "accounts.file", None))
            if accountfile:
                accountfile = abspath_expanduser_unicode(accountfile, base=self.basedir)
            accounturl = self.get_config("sftpd", "accounts.url", None)
            sftp_portstr = self.get_config("sftpd", "port", "8022")
            pubkey_file = from_utf8_or_none(self.get_config("sftpd", "host_pubkey_file"))
            privkey_file = from_utf8_or_none(self.get_config("sftpd", "host_privkey_file"))

            from allmydata.frontends import sftpd
            s = sftpd.SFTPServer(self, accountfile, accounturl,
                                 sftp_portstr, pubkey_file, privkey_file)
            s.setServiceParent(self)

    def init_magic_folder(self):
        #print "init_magic_folder"
        if self.get_config("drop_upload", "enabled", False, boolean=True):
            raise OldConfigOptionError("The [drop_upload] section must be renamed to [magic_folder].\n"
                                       "See docs/frontends/magic-folder.rst for more information.")

        if self.get_config("magic_folder", "enabled", False, boolean=True):
            #print "magic folder enabled"
            from allmydata.frontends import magic_folder

            db_filename = os.path.join(self.basedir, "private", "magicfolderdb.sqlite")
            local_dir_config = self.get_config("magic_folder", "local.directory").decode("utf-8")
            try:
                poll_interval = int(self.get_config("magic_folder", "poll_interval", 3))
            except ValueError:
                raise ValueError("[magic_folder] poll_interval must be an int")

            s = magic_folder.MagicFolder(
                client=self,
                upload_dircap=self.get_private_config("magic_folder_dircap"),
                collective_dircap=self.get_private_config("collective_dircap"),
                local_path_u=abspath_expanduser_unicode(local_dir_config, base=self.basedir),
                dbfile=abspath_expanduser_unicode(db_filename),
                umask=self.get_config("magic_folder", "download.umask", 0077),
                downloader_delay=poll_interval,
            )
            self._magic_folder = s
            s.setServiceParent(self)
            s.startService()

            # start processing the upload queue when we've connected to
            # enough servers
            threshold = min(self.encoding_params["k"],
                            self.encoding_params["happy"] + 1)
            d = self.storage_broker.when_connected_enough(threshold)
            d.addCallback(lambda ign: s.ready())

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

    def upload(self, uploadable):
        uploader = self.getServiceNamed("uploader")
        return uploader.upload(uploadable)
