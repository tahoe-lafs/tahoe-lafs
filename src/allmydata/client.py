import os, stat, time
from allmydata.interfaces import RIStorageServer
from allmydata import node

from zope.interface import implements
from twisted.internet import reactor, defer
from twisted.application.internet import TimerService
from foolscap.api import Referenceable
from pycryptopp.publickey import rsa

import allmydata
from allmydata.storage.server import StorageServer
from allmydata import storage_client
from allmydata.immutable.upload import Uploader
from allmydata.immutable.download import Downloader
from allmydata.immutable.offloaded import Helper
from allmydata.control import ControlServer
from allmydata.introducer.client import IntroducerClient
from allmydata.util import hashutil, base32, pollmixin, cachedir, log
from allmydata.util.abbreviate import parse_abbreviated_size
from allmydata.util.time_format import parse_duration, parse_date
from allmydata.stats import StatsProvider
from allmydata.history import History
from allmydata.interfaces import IStatsProducer, RIStubClient
from allmydata.nodemaker import NodeMaker


KiB=1024
MiB=1024*KiB
GiB=1024*MiB
TiB=1024*GiB
PiB=1024*TiB

class StubClient(Referenceable):
    implements(RIStubClient)

def _make_secret():
    return base32.b2a(os.urandom(hashutil.CRYPTO_VAL_SIZE)) + "\n"

class SecretHolder:
    def __init__(self, lease_secret):
        self._lease_secret = lease_secret

    def get_renewal_secret(self):
        return hashutil.my_renewal_secret_hash(self._lease_secret)

    def get_cancel_secret(self):
        return hashutil.my_cancel_secret_hash(self._lease_secret)

class KeyGenerator:
    def __init__(self):
        self._remote = None
        self.default_keysize = 2048

    def set_remote_generator(self, keygen):
        self._remote = keygen
    def set_default_keysize(self, keysize):
        """Call this to override the size of the RSA keys created for new
        mutable files. The default of None means to let mutable.filenode
        choose its own size, which means 2048 bits."""
        self.default_keysize = keysize

    def generate(self, keysize=None):
        keysize = keysize or self.default_keysize
        if self._remote:
            d = self._remote.callRemote('get_rsa_key_pair', keysize)
            def make_key_objs((verifying_key, signing_key)):
                v = rsa.create_verifying_key_from_string(verifying_key)
                s = rsa.create_signing_key_from_string(signing_key)
                return v, s
            d.addCallback(make_key_objs)
            return d
        else:
            # RSA key generation for a 2048 bit key takes between 0.8 and 3.2
            # secs
            signer = rsa.generate(keysize)
            verifier = signer.get_verifying_key()
            return defer.succeed( (verifier, signer) )


class Client(node.Node, pollmixin.PollMixin):
    implements(IStatsProducer)

    PORTNUMFILE = "client.port"
    STOREDIR = 'storage'
    NODETYPE = "client"
    SUICIDE_PREVENTION_HOTLINE_FILE = "suicide_prevention_hotline"

    # This means that if a storage server treats me as though I were a
    # 1.0.0 storage client, it will work as they expect.
    OLDEST_SUPPORTED_VERSION = "1.0.0"

    # this is a tuple of (needed, desired, total, max_segment_size). 'needed'
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
        self.started_timestamp = time.time()
        self.logSource="Client"
        self.DEFAULT_ENCODING_PARAMETERS = self.DEFAULT_ENCODING_PARAMETERS.copy()
        self.init_introducer_client()
        self.init_stats_provider()
        self.init_lease_secret()
        self.init_storage()
        self.init_control()
        self.helper = None
        if self.get_config("helper", "enabled", False, boolean=True):
            self.init_helper()
        self._key_generator = KeyGenerator()
        key_gen_furl = self.get_config("client", "key_generator.furl", None)
        if key_gen_furl:
            self.init_key_gen(key_gen_furl)
        self.init_client()
        # ControlServer and Helper are attached after Tub startup
        self.init_ftp_server()
        self.init_sftp_server()

        hotline_file = os.path.join(self.basedir,
                                    self.SUICIDE_PREVENTION_HOTLINE_FILE)
        if os.path.exists(hotline_file):
            age = time.time() - os.stat(hotline_file)[stat.ST_MTIME]
            self.log("hotline file noticed (%ds old), starting timer" % age)
            hotline = TimerService(1.0, self._check_hotline, hotline_file)
            hotline.setServiceParent(self)

        # this needs to happen last, so it can use getServiceNamed() to
        # acquire references to StorageServer and other web-statusable things
        webport = self.get_config("node", "web.port", None)
        if webport:
            self.init_web(webport) # strports string

    def read_old_config_files(self):
        node.Node.read_old_config_files(self)
        copy = self._copy_config_from_file
        copy("introducer.furl", "client", "introducer.furl")
        copy("helper.furl", "client", "helper.furl")
        copy("key_generator.furl", "client", "key_generator.furl")
        copy("stats_gatherer.furl", "client", "stats_gatherer.furl")
        if os.path.exists(os.path.join(self.basedir, "no_storage")):
            self.set_config("storage", "enabled", "false")
        if os.path.exists(os.path.join(self.basedir, "readonly_storage")):
            self.set_config("storage", "readonly", "true")
        if os.path.exists(os.path.join(self.basedir, "debug_discard_storage")):
            self.set_config("storage", "debug_discard", "true")
        if os.path.exists(os.path.join(self.basedir, "run_helper")):
            self.set_config("helper", "enabled", "true")

    def init_introducer_client(self):
        self.introducer_furl = self.get_config("client", "introducer.furl")
        ic = IntroducerClient(self.tub, self.introducer_furl,
                              self.nickname,
                              str(allmydata.__full_version__),
                              str(self.OLDEST_SUPPORTED_VERSION))
        self.introducer_client = ic
        # hold off on starting the IntroducerClient until our tub has been
        # started, so we'll have a useful address on our RemoteReference, so
        # that the introducer's status page will show us.
        d = self.when_tub_ready()
        def _start_introducer_client(res):
            ic.setServiceParent(self)
        d.addCallback(_start_introducer_client)
        d.addErrback(log.err, facility="tahoe.init",
                     level=log.BAD, umid="URyI5w")

    def init_stats_provider(self):
        gatherer_furl = self.get_config("client", "stats_gatherer.furl", None)
        self.stats_provider = StatsProvider(self, gatherer_furl)
        self.add_service(self.stats_provider)
        self.stats_provider.register_producer(self)

    def get_stats(self):
        return { 'node.uptime': time.time() - self.started_timestamp }

    def init_lease_secret(self):
        secret_s = self.get_or_create_private_config("secret", _make_secret)
        lease_secret = base32.a2b(secret_s)
        self._secret_holder = SecretHolder(lease_secret)

    def init_storage(self):
        # should we run a storage server (and publish it for others to use)?
        if not self.get_config("storage", "enabled", True, boolean=True):
            return
        readonly = self.get_config("storage", "readonly", False, boolean=True)

        storedir = os.path.join(self.basedir, self.STOREDIR)

        data = self.get_config("storage", "reserved_space", None)
        reserved = None
        try:
            reserved = parse_abbreviated_size(data)
        except ValueError:
            log.msg("[storage]reserved_space= contains unparseable value %s"
                    % data)
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

        d = self.when_tub_ready()
        # we can't do registerReference until the Tub is ready
        def _publish(res):
            furl_file = os.path.join(self.basedir, "private", "storage.furl")
            furl = self.tub.registerReference(ss, furlFile=furl_file)
            ri_name = RIStorageServer.__remote_name__
            self.introducer_client.publish(furl, "storage", ri_name)
        d.addCallback(_publish)
        d.addErrback(log.err, facility="tahoe.init",
                     level=log.BAD, umid="aLGBKw")

    def init_client(self):
        helper_furl = self.get_config("client", "helper.furl", None)
        DEP = self.DEFAULT_ENCODING_PARAMETERS
        DEP["k"] = int(self.get_config("client", "shares.needed", DEP["k"]))
        DEP["n"] = int(self.get_config("client", "shares.total", DEP["n"]))
        DEP["happy"] = int(self.get_config("client", "shares.happy", DEP["happy"]))
        convergence_s = self.get_or_create_private_config('convergence', _make_secret)
        self.convergence = base32.a2b(convergence_s)

        self.init_client_storage_broker()
        self.history = History(self.stats_provider)
        self.add_service(Uploader(helper_furl, self.stats_provider))
        download_cachedir = os.path.join(self.basedir,
                                         "private", "cache", "download")
        self.download_cache_dirman = cachedir.CacheDirectoryManager(download_cachedir)
        self.download_cache_dirman.setServiceParent(self)
        self.downloader = Downloader(self.storage_broker, self.stats_provider)
        self.init_stub_client()
        self.init_nodemaker()

    def init_client_storage_broker(self):
        # create a StorageFarmBroker object, for use by Uploader/Downloader
        # (and everybody else who wants to use storage servers)
        sb = storage_client.StorageFarmBroker(self.tub, permute_peers=True)
        self.storage_broker = sb

        # load static server specifications from tahoe.cfg, if any.
        # Not quite ready yet.
        #if self.config.has_section("client-server-selection"):
        #    server_params = {} # maps serverid to dict of parameters
        #    for (name, value) in self.config.items("client-server-selection"):
        #        pieces = name.split(".")
        #        if pieces[0] == "server":
        #            serverid = pieces[1]
        #            if serverid not in server_params:
        #                server_params[serverid] = {}
        #            server_params[serverid][pieces[2]] = value
        #    for serverid, params in server_params.items():
        #        server_type = params.pop("type")
        #        if server_type == "tahoe-foolscap":
        #            s = storage_client.NativeStorageClient(*params)
        #        else:
        #            msg = ("unrecognized server type '%s' in "
        #                   "tahoe.cfg [client-server-selection]server.%s.type"
        #                   % (server_type, serverid))
        #            raise storage_client.UnknownServerTypeError(msg)
        #        sb.add_server(s.serverid, s)

        # check to see if we're supposed to use the introducer too
        if self.get_config("client-server-selection", "use_introducer",
                           default=True, boolean=True):
            sb.use_introducer(self.introducer_client)

    def get_storage_broker(self):
        return self.storage_broker

    def init_stub_client(self):
        def _publish(res):
            # we publish an empty object so that the introducer can count how
            # many clients are connected and see what versions they're
            # running.
            sc = StubClient()
            furl = self.tub.registerReference(sc)
            ri_name = RIStubClient.__remote_name__
            self.introducer_client.publish(furl, "stub_client", ri_name)
        d = self.when_tub_ready()
        d.addCallback(_publish)
        d.addErrback(log.err, facility="tahoe.init",
                     level=log.BAD, umid="OEHq3g")

    def init_nodemaker(self):
        self.nodemaker = NodeMaker(self.storage_broker,
                                   self._secret_holder,
                                   self.get_history(),
                                   self.getServiceNamed("uploader"),
                                   self.downloader,
                                   self.download_cache_dirman,
                                   self.get_encoding_parameters(),
                                   self._key_generator)

    def get_history(self):
        return self.history

    def init_control(self):
        d = self.when_tub_ready()
        def _publish(res):
            c = ControlServer()
            c.setServiceParent(self)
            control_url = self.tub.registerReference(c)
            self.write_private_config("control.furl", control_url + "\n")
        d.addCallback(_publish)
        d.addErrback(log.err, facility="tahoe.init",
                     level=log.BAD, umid="d3tNXA")

    def init_helper(self):
        d = self.when_tub_ready()
        def _publish(self):
            self.helper = Helper(os.path.join(self.basedir, "helper"),
                                 self.storage_broker, self._secret_holder,
                                 self.stats_provider, self.history)
            # TODO: this is confusing. BASEDIR/private/helper.furl is created
            # by the helper. BASEDIR/helper.furl is consumed by the client
            # who wants to use the helper. I like having the filename be the
            # same, since that makes 'cp' work smoothly, but the difference
            # between config inputs and generated outputs is hard to see.
            helper_furlfile = os.path.join(self.basedir,
                                           "private", "helper.furl")
            self.tub.registerReference(self.helper, furlFile=helper_furlfile)
        d.addCallback(_publish)
        d.addErrback(log.err, facility="tahoe.init",
                     level=log.BAD, umid="K0mW5w")

    def init_key_gen(self, key_gen_furl):
        d = self.when_tub_ready()
        def _subscribe(self):
            self.tub.connectTo(key_gen_furl, self._got_key_generator)
        d.addCallback(_subscribe)
        d.addErrback(log.err, facility="tahoe.init",
                     level=log.BAD, umid="z9DMzw")

    def _got_key_generator(self, key_generator):
        self._key_generator.set_remote_generator(key_generator)
        key_generator.notifyOnDisconnect(self._lost_key_generator)

    def _lost_key_generator(self):
        self._key_generator.set_remote_generator(None)

    def set_default_mutable_keysize(self, keysize):
        self._key_generator.set_default_keysize(keysize)

    def init_web(self, webport):
        self.log("init_web(webport=%s)", args=(webport,))

        from allmydata.webish import WebishServer
        nodeurl_path = os.path.join(self.basedir, "node.url")
        staticdir = self.get_config("node", "web.static", "public_html")
        staticdir = os.path.expanduser(staticdir)
        ws = WebishServer(self, webport, nodeurl_path, staticdir)
        self.add_service(ws)

    def init_ftp_server(self):
        if self.get_config("ftpd", "enabled", False, boolean=True):
            accountfile = self.get_config("ftpd", "accounts.file", None)
            accounturl = self.get_config("ftpd", "accounts.url", None)
            ftp_portstr = self.get_config("ftpd", "port", "8021")

            from allmydata.frontends import ftpd
            s = ftpd.FTPServer(self, accountfile, accounturl, ftp_portstr)
            s.setServiceParent(self)

    def init_sftp_server(self):
        if self.get_config("sftpd", "enabled", False, boolean=True):
            accountfile = self.get_config("sftpd", "accounts.file", None)
            accounturl = self.get_config("sftpd", "accounts.url", None)
            sftp_portstr = self.get_config("sftpd", "port", "8022")
            pubkey_file = self.get_config("sftpd", "host_pubkey_file")
            privkey_file = self.get_config("sftpd", "host_privkey_file")

            from allmydata.frontends import sftpd
            s = sftpd.SFTPServer(self, accountfile, accounturl,
                                 sftp_portstr, pubkey_file, privkey_file)
            s.setServiceParent(self)

    def _check_hotline(self, hotline_file):
        if os.path.exists(hotline_file):
            mtime = os.stat(hotline_file)[stat.ST_MTIME]
            if mtime > time.time() - 120.0:
                return
            else:
                self.log("hotline file too old, shutting down")
        else:
            self.log("hotline file missing, shutting down")
        reactor.stop()

    def get_encoding_parameters(self):
        return self.DEFAULT_ENCODING_PARAMETERS

    def connected_to_introducer(self):
        if self.introducer_client:
            return self.introducer_client.connected_to_introducer()
        return False

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
            return len(self.storage_broker.get_all_servers()) >= num_clients
        d = self.poll(_check, 0.5)
        d.addCallback(lambda res: None)
        return d


    # these four methods are the primitives for creating filenodes and
    # dirnodes. The first takes a URI and produces a filenode or (new-style)
    # dirnode. The other three create brand-new filenodes/dirnodes.

    def create_node_from_uri(self, writecap, readcap=None):
        # this returns synchronously.
        return self.nodemaker.create_from_cap(writecap, readcap)

    def create_dirnode(self, initial_children={}):
        d = self.nodemaker.create_new_mutable_directory()
        assert not initial_children, "not ready yet: %s" % (initial_children,)
        if initial_children:
            d.addCallback(lambda n: n.set_children(initial_children))
        return d

    def create_mutable_file(self, contents="", keysize=None):
        return self.nodemaker.create_mutable_file(contents, keysize)

    def upload(self, uploadable):
        uploader = self.getServiceNamed("uploader")
        return uploader.upload(uploadable, history=self.get_history())
