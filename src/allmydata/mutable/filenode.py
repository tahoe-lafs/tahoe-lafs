
import random

from zope.interface import implements
from twisted.internet import defer, reactor
from foolscap.eventual import eventually
from allmydata.interfaces import IMutableFileNode, IMutableFileURI, \
     ICheckable, ICheckResults, NotEnoughSharesError
from allmydata.util import hashutil, log
from allmydata.util.assertutil import precondition
from allmydata.uri import WriteableSSKFileURI
from allmydata.monitor import Monitor
from pycryptopp.publickey import rsa
from pycryptopp.cipher.aes import AES

from publish import Publish
from common import MODE_READ, MODE_WRITE, UnrecoverableFileError, \
     ResponseCache, UncoordinatedWriteError
from servermap import ServerMap, ServermapUpdater
from retrieve import Retrieve
from checker import MutableChecker, MutableCheckAndRepairer
from repairer import Repairer


class BackoffAgent:
    # these parameters are copied from foolscap.reconnector, which gets them
    # from twisted.internet.protocol.ReconnectingClientFactory
    initialDelay = 1.0
    factor = 2.7182818284590451 # (math.e)
    jitter = 0.11962656492 # molar Planck constant times c, Joule meter/mole
    maxRetries = 4

    def __init__(self):
        self._delay = self.initialDelay
        self._count = 0
    def delay(self, node, f):
        self._count += 1
        if self._count == 4:
            return f
        self._delay = self._delay * self.factor
        self._delay = random.normalvariate(self._delay,
                                           self._delay * self.jitter)
        d = defer.Deferred()
        reactor.callLater(self._delay, d.callback, None)
        return d

# use client.create_mutable_file() to make one of these

class MutableFileNode:
    implements(IMutableFileNode, ICheckable)
    SIGNATURE_KEY_SIZE = 2048
    checker_class = MutableChecker
    check_and_repairer_class = MutableCheckAndRepairer

    def __init__(self, client):
        self._client = client
        self._pubkey = None # filled in upon first read
        self._privkey = None # filled in if we're mutable
        # we keep track of the last encoding parameters that we use. These
        # are updated upon retrieve, and used by publish. If we publish
        # without ever reading (i.e. overwrite()), then we use these values.
        defaults = client.get_encoding_parameters()
        self._required_shares = defaults["k"]
        self._total_shares = defaults["n"]
        self._sharemap = {} # known shares, shnum-to-[nodeids]
        self._cache = ResponseCache()

        # all users of this MutableFileNode go through the serializer. This
        # takes advantage of the fact that Deferreds discard the callbacks
        # that they're done with, so we can keep using the same Deferred
        # forever without consuming more and more memory.
        self._serializer = defer.succeed(None)

    def __repr__(self):
        if hasattr(self, '_uri'):
            return "<%s %x %s %s>" % (self.__class__.__name__, id(self), self.is_readonly() and 'RO' or 'RW', self._uri.abbrev())
        else:
            return "<%s %x %s %s>" % (self.__class__.__name__, id(self), None, None)

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

        d = defer.maybeDeferred(self._generate_pubprivkeys, keypair_generator)
        d.addCallback(self._generated)
        d.addCallback(lambda res: self._upload(initial_contents, None))
        return d

    def _generated(self, (pubkey, privkey) ):
        self._pubkey, self._privkey = pubkey, privkey
        pubkey_s = self._pubkey.serialize()
        privkey_s = self._privkey.serialize()
        self._writekey = hashutil.ssk_writekey_hash(privkey_s)
        self._encprivkey = self._encrypt_privkey(self._writekey, privkey_s)
        self._fingerprint = hashutil.ssk_pubkey_fingerprint_hash(pubkey_s)
        self._uri = WriteableSSKFileURI(self._writekey, self._fingerprint)
        self._readkey = self._uri.readkey
        self._storage_index = self._uri.storage_index

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

    def _populate_pubkey(self, pubkey):
        self._pubkey = pubkey
    def _populate_required_shares(self, required_shares):
        self._required_shares = required_shares
    def _populate_total_shares(self, total_shares):
        self._total_shares = total_shares

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

    ####################################
    # IFilesystemNode

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

    def get_verify_cap(self):
        return IMutableFileURI(self._uri).get_verify_cap()

    def _do_serialized(self, cb, *args, **kwargs):
        # note: to avoid deadlock, this callable is *not* allowed to invoke
        # other serialized methods within this (or any other)
        # MutableFileNode. The callable should be a bound method of this same
        # MFN instance.
        d = defer.Deferred()
        self._serializer.addCallback(lambda ignore: cb(*args, **kwargs))
        # we need to put off d.callback until this Deferred is finished being
        # processed. Otherwise the caller's subsequent activities (like,
        # doing other things with this node) can cause reentrancy problems in
        # the Deferred code itself
        self._serializer.addBoth(lambda res: eventually(d.callback, res))
        # add a log.err just in case something really weird happens, because
        # self._serializer stays around forever, therefore we won't see the
        # usual Unhandled Error in Deferred that would give us a hint.
        self._serializer.addErrback(log.err)
        return d

    #################################
    # ICheckable

    def check(self, monitor, verify=False):
        checker = self.checker_class(self, monitor)
        return checker.check(verify)

    def check_and_repair(self, monitor, verify=False):
        checker = self.check_and_repairer_class(self, monitor)
        return checker.check(verify)

    #################################
    # IRepairable

    def repair(self, check_results, force=False):
        assert ICheckResults(check_results)
        r = Repairer(self, check_results)
        d = r.start(force)
        return d


    #################################
    # IMutableFileNode

    # allow the use of IDownloadTarget
    def download(self, target):
        # fake it. TODO: make this cleaner.
        d = self.download_best_version()
        def _done(data):
            target.open(len(data))
            target.write(data)
            target.close()
            return target.finish()
        d.addCallback(_done)
        return d


    # new API

    def download_best_version(self):
        return self._do_serialized(self._download_best_version)
    def _download_best_version(self):
        servermap = ServerMap()
        d = self._try_once_to_download_best_version(servermap, MODE_READ)
        def _maybe_retry(f):
            f.trap(NotEnoughSharesError)
            # the download is worth retrying once. Make sure to use the
            # old servermap, since it is what remembers the bad shares,
            # but use MODE_WRITE to make it look for even more shares.
            # TODO: consider allowing this to retry multiple times.. this
            # approach will let us tolerate about 8 bad shares, I think.
            return self._try_once_to_download_best_version(servermap,
                                                           MODE_WRITE)
        d.addErrback(_maybe_retry)
        return d
    def _try_once_to_download_best_version(self, servermap, mode):
        d = self._update_servermap(servermap, mode)
        d.addCallback(self._once_updated_download_best_version, servermap)
        return d
    def _once_updated_download_best_version(self, ignored, servermap):
        goal = servermap.best_recoverable_version()
        if not goal:
            raise UnrecoverableFileError("no recoverable versions")
        return self._try_once_to_download_version(servermap, goal)

    def get_size_of_best_version(self):
        d = self.get_servermap(MODE_READ)
        def _got_servermap(smap):
            ver = smap.best_recoverable_version()
            if not ver:
                raise UnrecoverableFileError("no recoverable version")
            return smap.size_of_version(ver)
        d.addCallback(_got_servermap)
        return d

    def overwrite(self, new_contents):
        return self._do_serialized(self._overwrite, new_contents)
    def _overwrite(self, new_contents):
        servermap = ServerMap()
        d = self._update_servermap(servermap, mode=MODE_WRITE)
        d.addCallback(lambda ignored: self._upload(new_contents, servermap))
        return d


    def modify(self, modifier, backoffer=None):
        """I use a modifier callback to apply a change to the mutable file.
        I implement the following pseudocode::

         obtain_mutable_filenode_lock()
         first_time = True
         while True:
           update_servermap(MODE_WRITE)
           old = retrieve_best_version()
           new = modifier(old, servermap, first_time)
           first_time = False
           if new == old: break
           try:
             publish(new)
           except UncoordinatedWriteError, e:
             backoffer(e)
             continue
           break
         release_mutable_filenode_lock()

        The idea is that your modifier function can apply a delta of some
        sort, and it will be re-run as necessary until it succeeds. The
        modifier must inspect the old version to see whether its delta has
        already been applied: if so it should return the contents unmodified.

        Note that the modifier is required to run synchronously, and must not
        invoke any methods on this MutableFileNode instance.

        The backoff-er is a callable that is responsible for inserting a
        random delay between subsequent attempts, to help competing updates
        from colliding forever. It is also allowed to give up after a while.
        The backoffer is given two arguments: this MutableFileNode, and the
        Failure object that contains the UncoordinatedWriteError. It should
        return a Deferred that will fire when the next attempt should be
        made, or return the Failure if the loop should give up. If
        backoffer=None, a default one is provided which will perform
        exponential backoff, and give up after 4 tries. Note that the
        backoffer should not invoke any methods on this MutableFileNode
        instance, and it needs to be highly conscious of deadlock issues.
        """
        return self._do_serialized(self._modify, modifier, backoffer)
    def _modify(self, modifier, backoffer):
        servermap = ServerMap()
        if backoffer is None:
            backoffer = BackoffAgent().delay
        return self._modify_and_retry(servermap, modifier, backoffer, True)
    def _modify_and_retry(self, servermap, modifier, backoffer, first_time):
        d = self._modify_once(servermap, modifier, first_time)
        def _retry(f):
            f.trap(UncoordinatedWriteError)
            d2 = defer.maybeDeferred(backoffer, self, f)
            d2.addCallback(lambda ignored:
                           self._modify_and_retry(servermap, modifier,
                                                  backoffer, False))
            return d2
        d.addErrback(_retry)
        return d
    def _modify_once(self, servermap, modifier, first_time):
        d = self._update_servermap(servermap, MODE_WRITE)
        d.addCallback(self._once_updated_download_best_version, servermap)
        def _apply(old_contents):
            new_contents = modifier(old_contents, servermap, first_time)
            if new_contents is None or new_contents == old_contents:
                # no changes need to be made
                if first_time:
                    return
                # However, since Publish is not automatically doing a
                # recovery when it observes UCWE, we need to do a second
                # publish. See #551 for details. We'll basically loop until
                # we managed an uncontested publish.
                new_contents = old_contents
            precondition(isinstance(new_contents, str),
                         "Modifier function must return a string or None")
            return self._upload(new_contents, servermap)
        d.addCallback(_apply)
        return d

    def get_servermap(self, mode):
        return self._do_serialized(self._get_servermap, mode)
    def _get_servermap(self, mode):
        servermap = ServerMap()
        return self._update_servermap(servermap, mode)
    def _update_servermap(self, servermap, mode):
        u = ServermapUpdater(self, Monitor(), servermap, mode)
        history = self._client.get_history()
        if history:
            history.notify_mapupdate(u.get_status())
        return u.update()

    def download_version(self, servermap, version, fetch_privkey=False):
        return self._do_serialized(self._try_once_to_download_version,
                                   servermap, version, fetch_privkey)
    def _try_once_to_download_version(self, servermap, version,
                                      fetch_privkey=False):
        r = Retrieve(self, servermap, version, fetch_privkey)
        history = self._client.get_history()
        if history:
            history.notify_retrieve(r.get_status())
        return r.download()

    def upload(self, new_contents, servermap):
        return self._do_serialized(self._upload, new_contents, servermap)
    def _upload(self, new_contents, servermap):
        assert self._pubkey, "update_servermap must be called before publish"
        p = Publish(self, servermap)
        history = self._client.get_history()
        if history:
            history.notify_publish(p.get_status(), len(new_contents))
        return p.publish(new_contents)
