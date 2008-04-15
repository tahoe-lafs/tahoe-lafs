
import weakref
from twisted.application import service

from zope.interface import implements
from twisted.internet import defer
from allmydata.interfaces import IMutableFileNode, IMutableFileURI
from allmydata.util import hashutil
from allmydata.uri import WriteableSSKFileURI
from allmydata.encode import NotEnoughSharesError
from pycryptopp.publickey import rsa
from pycryptopp.cipher.aes import AES

from publish import Publish
from common import MODE_ENOUGH, MODE_WRITE, UnrecoverableFileError, \
     ResponseCache
from servermap import ServerMap, ServermapUpdater
from retrieve import Retrieve


# use client.create_mutable_file() to make one of these

class MutableFileNode:
    implements(IMutableFileNode)
    publish_class = Publish
    retrieve_class = Retrieve
    SIGNATURE_KEY_SIZE = 2048
    DEFAULT_ENCODING = (3, 10)

    def __init__(self, client):
        self._client = client
        self._pubkey = None # filled in upon first read
        self._privkey = None # filled in if we're mutable
        # we keep track of the last encoding parameters that we use. These
        # are updated upon retrieve, and used by publish. If we publish
        # without ever reading (i.e. overwrite()), then we use these values.
        (self._required_shares, self._total_shares) = self.DEFAULT_ENCODING
        self._sharemap = {} # known shares, shnum-to-[nodeids]
        self._cache = ResponseCache()

        self._current_data = None # SDMF: we're allowed to cache the contents
        self._current_roothash = None # ditto
        self._current_seqnum = None # ditto

    def __repr__(self):
        return "<%s %x %s %s>" % (self.__class__.__name__, id(self), self.is_readonly() and 'RO' or 'RW', hasattr(self, '_uri') and self._uri.abbrev())

    def init_from_uri(self, myuri):
        # we have the URI, but we have not yet retrieved the public
        # verification key, nor things like 'k' or 'N'. If and when someone
        # wants to get our contents, we'll pull from shares and fill those
        # in.
        self._uri = IMutableFileURI(myuri)
        if not self._uri.is_readonly():
            self._writekey = self._uri.writekey
        self._readkey = self._uri.readkey
        self._storage_index = self._uri.storage_index
        self._fingerprint = self._uri.fingerprint
        # the following values are learned during Retrieval
        #  self._pubkey
        #  self._required_shares
        #  self._total_shares
        # and these are needed for Publish. They are filled in by Retrieval
        # if possible, otherwise by the first peer that Publish talks to.
        self._privkey = None
        self._encprivkey = None
        return self

    def create(self, initial_contents, keypair_generator=None):
        """Call this when the filenode is first created. This will generate
        the keys, generate the initial shares, wait until at least numpeers
        are connected, allocate shares, and upload the initial
        contents. Returns a Deferred that fires (with the MutableFileNode
        instance you should use) when it completes.
        """
        self._required_shares, self._total_shares = self.DEFAULT_ENCODING

        d = defer.maybeDeferred(self._generate_pubprivkeys, keypair_generator)
        def _generated( (pubkey, privkey) ):
            self._pubkey, self._privkey = pubkey, privkey
            pubkey_s = self._pubkey.serialize()
            privkey_s = self._privkey.serialize()
            self._writekey = hashutil.ssk_writekey_hash(privkey_s)
            self._encprivkey = self._encrypt_privkey(self._writekey, privkey_s)
            self._fingerprint = hashutil.ssk_pubkey_fingerprint_hash(pubkey_s)
            self._uri = WriteableSSKFileURI(self._writekey, self._fingerprint)
            self._readkey = self._uri.readkey
            self._storage_index = self._uri.storage_index
            # TODO: seqnum/roothash: really we mean "doesn't matter since
            # nobody knows about us yet"
            self._current_seqnum = 0
            self._current_roothash = "\x00"*32
            return self._publish(initial_contents)
        d.addCallback(_generated)
        return d

    def _generate_pubprivkeys(self, keypair_generator):
        if keypair_generator:
            return keypair_generator(self.SIGNATURE_KEY_SIZE)
        else:
            # RSA key generation for a 2048 bit key takes between 0.8 and 3.2 secs
            signer = rsa.generate(self.SIGNATURE_KEY_SIZE)
            verifier = signer.get_verifying_key()
            return verifier, signer

    def _encrypt_privkey(self, writekey, privkey):
        enc = AES(writekey)
        crypttext = enc.process(privkey)
        return crypttext

    def _decrypt_privkey(self, enc_privkey):
        enc = AES(self._writekey)
        privkey = enc.process(enc_privkey)
        return privkey

    def _populate(self, stuff):
        # the Retrieval object calls this with values it discovers when
        # downloading the slot. This is how a MutableFileNode that was
        # created from a URI learns about its full key.
        pass

    def _populate_pubkey(self, pubkey):
        self._pubkey = pubkey
    def _populate_required_shares(self, required_shares):
        self._required_shares = required_shares
    def _populate_total_shares(self, total_shares):
        self._total_shares = total_shares
    def _populate_seqnum(self, seqnum):
        self._current_seqnum = seqnum
    def _populate_root_hash(self, root_hash):
        self._current_roothash = root_hash

    def _populate_privkey(self, privkey):
        self._privkey = privkey
    def _populate_encprivkey(self, encprivkey):
        self._encprivkey = encprivkey


    def get_write_enabler(self, peerid):
        assert len(peerid) == 20
        return hashutil.ssk_write_enabler_hash(self._writekey, peerid)
    def get_renewal_secret(self, peerid):
        assert len(peerid) == 20
        crs = self._client.get_renewal_secret()
        frs = hashutil.file_renewal_secret_hash(crs, self._storage_index)
        return hashutil.bucket_renewal_secret_hash(frs, peerid)
    def get_cancel_secret(self, peerid):
        assert len(peerid) == 20
        ccs = self._client.get_cancel_secret()
        fcs = hashutil.file_cancel_secret_hash(ccs, self._storage_index)
        return hashutil.bucket_cancel_secret_hash(fcs, peerid)

    def get_writekey(self):
        return self._writekey
    def get_readkey(self):
        return self._readkey
    def get_storage_index(self):
        return self._storage_index
    def get_privkey(self):
        return self._privkey
    def get_encprivkey(self):
        return self._encprivkey
    def get_pubkey(self):
        return self._pubkey

    def get_required_shares(self):
        return self._required_shares
    def get_total_shares(self):
        return self._total_shares


    def get_uri(self):
        return self._uri.to_string()
    def get_size(self):
        return "?" # TODO: this is likely to cause problems, not being an int
    def get_readonly(self):
        if self.is_readonly():
            return self
        ro = MutableFileNode(self._client)
        ro.init_from_uri(self._uri.get_readonly())
        return ro

    def get_readonly_uri(self):
        return self._uri.get_readonly().to_string()

    def is_mutable(self):
        return self._uri.is_mutable()
    def is_readonly(self):
        return self._uri.is_readonly()

    def __hash__(self):
        return hash((self.__class__, self._uri))
    def __cmp__(self, them):
        if cmp(type(self), type(them)):
            return cmp(type(self), type(them))
        if cmp(self.__class__, them.__class__):
            return cmp(self.__class__, them.__class__)
        return cmp(self._uri, them._uri)

    def get_verifier(self):
        return IMutableFileURI(self._uri).get_verifier()

    def obtain_lock(self, res=None):
        # stub, get real version from zooko's #265 patch
        d = defer.Deferred()
        d.callback(res)
        return d

    def release_lock(self, res):
        # stub
        return res

    ############################

    # methods exposed to the higher-layer application

    def update_servermap(self, old_map=None, mode=MODE_ENOUGH):
        servermap = old_map or ServerMap()
        d = self.obtain_lock()
        d.addCallback(lambda res:
                      ServermapUpdater(self, servermap, mode).update())
        d.addBoth(self.release_lock)
        return d

    def download_version(self, servermap, versionid):
        """Returns a Deferred that fires with a string."""
        d = self.obtain_lock()
        d.addCallback(lambda res:
                      Retrieve(self, servermap, versionid).download())
        d.addBoth(self.release_lock)
        return d

    def publish(self, servermap, newdata):
        assert self._pubkey, "update_servermap must be called before publish"
        d = self.obtain_lock()
        d.addCallback(lambda res: Publish(self, servermap).publish(newdata))
        # p = self.publish_class(self)
        # self._client.notify_publish(p)
        d.addBoth(self.release_lock)
        return d

    def modify(self, modifier, *args, **kwargs):
        """I use a modifier callback to apply a change to the mutable file.
        I implement the following pseudocode::

         obtain_mutable_filenode_lock()
         while True:
           update_servermap(MODE_WRITE)
           old = retrieve_best_version()
           new = modifier(old, *args, **kwargs)
           if new == old: break
           try:
             publish(new)
           except UncoordinatedWriteError:
             continue
           break
         release_mutable_filenode_lock()

        The idea is that your modifier function can apply a delta of some
        sort, and it will be re-run as necessary until it succeeds. The
        modifier must inspect the old version to see whether its delta has
        already been applied: if so it should return the contents unmodified.
        """
        NotImplementedError

    #################################

    def check(self):
        verifier = self.get_verifier()
        return self._client.getServiceNamed("checker").check(verifier)

    def download(self, target):
        # fake it. TODO: make this cleaner.
        d = self.download_to_data()
        def _done(data):
            target.open(len(data))
            target.write(data)
            target.close()
            return target.finish()
        d.addCallback(_done)
        return d

    def _update_and_retrieve_best(self, old_map=None):
        d = self.update_servermap(old_map=old_map, mode=MODE_ENOUGH)
        def _updated(smap):
            goal = smap.best_recoverable_version()
            if not goal:
                raise UnrecoverableFileError("no recoverable versions")
            return self.download_version(smap, goal)
        d.addCallback(_updated)
        return d

    def download_to_data(self):
        d = self.obtain_lock()
        d.addCallback(lambda res: self._update_and_retrieve_best())
        def _maybe_retry(f):
            f.trap(NotEnoughSharesError)
            e = f.value
            if not e.worth_retrying:
                return f
            # the download is worth retrying once. Make sure to use the old
            # servermap, since it is what remembers the bad shares. TODO:
            # consider allowing this to retry multiple times.. this approach
            # will let us tolerate about 8 bad shares, I think.
            return self._update_and_retrieve_best(e.servermap)
        d.addErrback(_maybe_retry)
        d.addBoth(self.release_lock)
        return d

    def _publish(self, initial_contents):
        p = Publish(self, None)
        d = p.publish(initial_contents)
        d.addCallback(lambda res: self)
        return d

    def update(self, newdata):
        d = self.obtain_lock()
        d.addCallback(lambda res: self.update_servermap(mode=MODE_WRITE))
        d.addCallback(lambda smap:
                      Publish(self, smap).publish(newdata))
        d.addBoth(self.release_lock)
        return d

    def overwrite(self, newdata):
        return self.update(newdata)


class MutableWatcher(service.MultiService):
    MAX_PUBLISH_STATUSES = 20
    MAX_RETRIEVE_STATUSES = 20
    name = "mutable-watcher"

    def __init__(self, stats_provider=None):
        service.MultiService.__init__(self)
        self.stats_provider = stats_provider
        self._all_publish = weakref.WeakKeyDictionary()
        self._recent_publish_status = []
        self._all_retrieve = weakref.WeakKeyDictionary()
        self._recent_retrieve_status = []

    def notify_publish(self, p):
        self._all_publish[p] = None
        self._recent_publish_status.append(p.get_status())
        if self.stats_provider:
            self.stats_provider.count('mutable.files_published', 1)
            #self.stats_provider.count('mutable.bytes_published', p._node.get_size())
        while len(self._recent_publish_status) > self.MAX_PUBLISH_STATUSES:
            self._recent_publish_status.pop(0)

    def list_all_publish(self):
        return self._all_publish.keys()
    def list_active_publish(self):
        return [p.get_status() for p in self._all_publish.keys()
                if p.get_status().get_active()]
    def list_recent_publish(self):
        return self._recent_publish_status


    def notify_retrieve(self, r):
        self._all_retrieve[r] = None
        self._recent_retrieve_status.append(r.get_status())
        if self.stats_provider:
            self.stats_provider.count('mutable.files_retrieved', 1)
            #self.stats_provider.count('mutable.bytes_retrieved', r._node.get_size())
        while len(self._recent_retrieve_status) > self.MAX_RETRIEVE_STATUSES:
            self._recent_retrieve_status.pop(0)

    def list_all_retrieve(self):
        return self._all_retrieve.keys()
    def list_active_retrieve(self):
        return [p.get_status() for p in self._all_retrieve.keys()
                if p.get_status().get_active()]
    def list_recent_retrieve(self):
        return self._recent_retrieve_status
