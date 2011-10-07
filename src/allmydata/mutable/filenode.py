
import random

from zope.interface import implements
from twisted.internet import defer, reactor
from foolscap.api import eventually
from allmydata.interfaces import IMutableFileNode, ICheckable, ICheckResults, \
     NotEnoughSharesError, MDMF_VERSION, SDMF_VERSION, IMutableUploadable, \
     IMutableFileVersion, IWriteable
from allmydata.util import hashutil, log, consumer, deferredutil, mathutil
from allmydata.util.assertutil import precondition
from allmydata.uri import WriteableSSKFileURI, ReadonlySSKFileURI, \
                          WriteableMDMFFileURI, ReadonlyMDMFFileURI
from allmydata.monitor import Monitor
from pycryptopp.cipher.aes import AES

from allmydata.mutable.publish import Publish, MutableData,\
                                      TransformingUploadable
from allmydata.mutable.common import MODE_READ, MODE_WRITE, MODE_CHECK, UnrecoverableFileError, \
     ResponseCache, UncoordinatedWriteError
from allmydata.mutable.servermap import ServerMap, ServermapUpdater
from allmydata.mutable.retrieve import Retrieve
from allmydata.mutable.checker import MutableChecker, MutableCheckAndRepairer
from allmydata.mutable.repairer import Repairer


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

# use nodemaker.create_mutable_file() to make one of these

class MutableFileNode:
    implements(IMutableFileNode, ICheckable)

    def __init__(self, storage_broker, secret_holder,
                 default_encoding_parameters, history):
        self._storage_broker = storage_broker
        self._secret_holder = secret_holder
        self._default_encoding_parameters = default_encoding_parameters
        self._history = history
        self._pubkey = None # filled in upon first read
        self._privkey = None # filled in if we're mutable
        # we keep track of the last encoding parameters that we use. These
        # are updated upon retrieve, and used by publish. If we publish
        # without ever reading (i.e. overwrite()), then we use these values.
        self._required_shares = default_encoding_parameters["k"]
        self._total_shares = default_encoding_parameters["n"]
        self._sharemap = {} # known shares, shnum-to-[nodeids]
        self._cache = ResponseCache()
        self._most_recent_size = None
        # filled in after __init__ if we're being created for the first time;
        # filled in by the servermap updater before publishing, otherwise.
        # set to this default value in case neither of those things happen,
        # or in case the servermap can't find any shares to tell us what
        # to publish as.
        self._protocol_version = None

        # all users of this MutableFileNode go through the serializer. This
        # takes advantage of the fact that Deferreds discard the callbacks
        # that they're done with, so we can keep using the same Deferred
        # forever without consuming more and more memory.
        self._serializer = defer.succeed(None)

        # Starting with MDMF, we can get these from caps if they're
        # there. Leave them alone for now; they'll be filled in by my
        # init_from_cap method if necessary.
        self._downloader_hints = {}

    def __repr__(self):
        if hasattr(self, '_uri'):
            return "<%s %x %s %s>" % (self.__class__.__name__, id(self), self.is_readonly() and 'RO' or 'RW', self._uri.abbrev())
        else:
            return "<%s %x %s %s>" % (self.__class__.__name__, id(self), None, None)

    def init_from_cap(self, filecap):
        # we have the URI, but we have not yet retrieved the public
        # verification key, nor things like 'k' or 'N'. If and when someone
        # wants to get our contents, we'll pull from shares and fill those
        # in.
        if isinstance(filecap, (WriteableMDMFFileURI, ReadonlyMDMFFileURI)):
            self._protocol_version = MDMF_VERSION
        elif isinstance(filecap, (ReadonlySSKFileURI, WriteableSSKFileURI)):
            self._protocol_version = SDMF_VERSION

        self._uri = filecap
        self._writekey = None

        if not filecap.is_readonly() and filecap.is_mutable():
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

    def create_with_keys(self, (pubkey, privkey), contents,
                         version=SDMF_VERSION):
        """Call this to create a brand-new mutable file. It will create the
        shares, find homes for them, and upload the initial contents (created
        with the same rules as IClient.create_mutable_file() ). Returns a
        Deferred that fires (with the MutableFileNode instance you should
        use) when it completes.
        """
        self._pubkey, self._privkey = pubkey, privkey
        pubkey_s = self._pubkey.serialize()
        privkey_s = self._privkey.serialize()
        self._writekey = hashutil.ssk_writekey_hash(privkey_s)
        self._encprivkey = self._encrypt_privkey(self._writekey, privkey_s)
        self._fingerprint = hashutil.ssk_pubkey_fingerprint_hash(pubkey_s)
        if version == MDMF_VERSION:
            self._uri = WriteableMDMFFileURI(self._writekey, self._fingerprint)
            self._protocol_version = version
        elif version == SDMF_VERSION:
            self._uri = WriteableSSKFileURI(self._writekey, self._fingerprint)
            self._protocol_version = version
        self._readkey = self._uri.readkey
        self._storage_index = self._uri.storage_index
        initial_contents = self._get_initial_contents(contents)
        return self._upload(initial_contents, None)

    def _get_initial_contents(self, contents):
        if contents is None:
            return MutableData("")

        if isinstance(contents, str):
            return MutableData(contents)

        if IMutableUploadable.providedBy(contents):
            return contents

        assert callable(contents), "%s should be callable, not %s" % \
               (contents, type(contents))
        return contents(self)

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
    def _add_to_cache(self, verinfo, shnum, offset, data):
        self._cache.add(verinfo, shnum, offset, data)
    def _read_from_cache(self, verinfo, shnum, offset, length):
        return self._cache.read(verinfo, shnum, offset, length)

    def get_write_enabler(self, peerid):
        assert len(peerid) == 20
        return hashutil.ssk_write_enabler_hash(self._writekey, peerid)
    def get_renewal_secret(self, peerid):
        assert len(peerid) == 20
        crs = self._secret_holder.get_renewal_secret()
        frs = hashutil.file_renewal_secret_hash(crs, self._storage_index)
        return hashutil.bucket_renewal_secret_hash(frs, peerid)
    def get_cancel_secret(self, peerid):
        assert len(peerid) == 20
        ccs = self._secret_holder.get_cancel_secret()
        fcs = hashutil.file_cancel_secret_hash(ccs, self._storage_index)
        return hashutil.bucket_cancel_secret_hash(fcs, peerid)

    def get_writekey(self):
        return self._writekey
    def get_readkey(self):
        return self._readkey
    def get_storage_index(self):
        return self._storage_index
    def get_fingerprint(self):
        return self._fingerprint
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

    def get_size(self):
        return self._most_recent_size

    def get_current_size(self):
        d = self.get_size_of_best_version()
        d.addCallback(self._stash_size)
        return d

    def _stash_size(self, size):
        self._most_recent_size = size
        return size

    def get_cap(self):
        return self._uri
    def get_readcap(self):
        return self._uri.get_readonly()
    def get_verify_cap(self):
        return self._uri.get_verify_cap()
    def get_repair_cap(self):
        if self._uri.is_readonly():
            return None
        return self._uri

    def get_uri(self):
        return self._uri.to_string()

    def get_write_uri(self):
        if self.is_readonly():
            return None
        return self._uri.to_string()

    def get_readonly_uri(self):
        return self._uri.get_readonly().to_string()

    def get_readonly(self):
        if self.is_readonly():
            return self
        ro = MutableFileNode(self._storage_broker, self._secret_holder,
                             self._default_encoding_parameters, self._history)
        ro.init_from_cap(self._uri.get_readonly())
        return ro

    def is_mutable(self):
        return self._uri.is_mutable()

    def is_readonly(self):
        return self._uri.is_readonly()

    def is_unknown(self):
        return False

    def is_allowed_in_immutable_directory(self):
        return not self._uri.is_mutable()

    def raise_error(self):
        pass

    def __hash__(self):
        return hash((self.__class__, self._uri))
    def __cmp__(self, them):
        if cmp(type(self), type(them)):
            return cmp(type(self), type(them))
        if cmp(self.__class__, them.__class__):
            return cmp(self.__class__, them.__class__)
        return cmp(self._uri, them._uri)


    #################################
    # ICheckable

    def check(self, monitor, verify=False, add_lease=False):
        checker = MutableChecker(self, self._storage_broker,
                                 self._history, monitor)
        return checker.check(verify, add_lease)

    def check_and_repair(self, monitor, verify=False, add_lease=False):
        checker = MutableCheckAndRepairer(self, self._storage_broker,
                                          self._history, monitor)
        return checker.check(verify, add_lease)

    #################################
    # IRepairable

    def repair(self, check_results, force=False):
        assert ICheckResults(check_results)
        r = Repairer(self, check_results)
        d = r.start(force)
        return d


    #################################
    # IFileNode

    def get_best_readable_version(self):
        """
        I return a Deferred that fires with a MutableFileVersion
        representing the best readable version of the file that I
        represent
        """
        return self.get_readable_version()


    def get_readable_version(self, servermap=None, version=None):
        """
        I return a Deferred that fires with an MutableFileVersion for my
        version argument, if there is a recoverable file of that version
        on the grid. If there is no recoverable version, I fire with an
        UnrecoverableFileError.

        If a servermap is provided, I look in there for the requested
        version. If no servermap is provided, I create and update a new
        one.

        If no version is provided, then I return a MutableFileVersion
        representing the best recoverable version of the file.
        """
        d = self._get_version_from_servermap(MODE_READ, servermap, version)
        def _build_version((servermap, their_version)):
            assert their_version in servermap.recoverable_versions()
            assert their_version in servermap.make_versionmap()

            mfv = MutableFileVersion(self,
                                     servermap,
                                     their_version,
                                     self._storage_index,
                                     self._storage_broker,
                                     self._readkey,
                                     history=self._history)
            assert mfv.is_readonly()
            mfv.set_downloader_hints(self._downloader_hints)
            # our caller can use this to download the contents of the
            # mutable file.
            return mfv
        return d.addCallback(_build_version)


    def _get_version_from_servermap(self,
                                    mode,
                                    servermap=None,
                                    version=None):
        """
        I return a Deferred that fires with (servermap, version).

        This function performs validation and a servermap update. If it
        returns (servermap, version), the caller can assume that:
            - servermap was last updated in mode.
            - version is recoverable, and corresponds to the servermap.

        If version and servermap are provided to me, I will validate
        that version exists in the servermap, and that the servermap was
        updated correctly.

        If version is not provided, but servermap is, I will validate
        the servermap and return the best recoverable version that I can
        find in the servermap.

        If the version is provided but the servermap isn't, I will
        obtain a servermap that has been updated in the correct mode and
        validate that version is found and recoverable.

        If neither servermap nor version are provided, I will obtain a
        servermap updated in the correct mode, and return the best
        recoverable version that I can find in there.
        """
        # XXX: wording ^^^^
        if servermap and servermap.last_update_mode == mode:
            d = defer.succeed(servermap)
        else:
            d = self._get_servermap(mode)

        def _get_version(servermap, v):
            if v and v not in servermap.recoverable_versions():
                v = None
            elif not v:
                v = servermap.best_recoverable_version()
            if not v:
                raise UnrecoverableFileError("no recoverable versions")

            return (servermap, v)
        return d.addCallback(_get_version, version)


    def download_best_version(self):
        """
        I return a Deferred that fires with the contents of the best
        version of this mutable file.
        """
        return self._do_serialized(self._download_best_version)


    def _download_best_version(self):
        """
        I am the serialized sibling of download_best_version.
        """
        d = self.get_best_readable_version()
        d.addCallback(self._record_size)
        d.addCallback(lambda version: version.download_to_data())

        # It is possible that the download will fail because there
        # aren't enough shares to be had. If so, we will try again after
        # updating the servermap in MODE_WRITE, which may find more
        # shares than updating in MODE_READ, as we just did. We can do
        # this by getting the best mutable version and downloading from
        # that -- the best mutable version will be a MutableFileVersion
        # with a servermap that was last updated in MODE_WRITE, as we
        # want. If this fails, then we give up.
        def _maybe_retry(failure):
            failure.trap(NotEnoughSharesError)

            d = self.get_best_mutable_version()
            d.addCallback(self._record_size)
            d.addCallback(lambda version: version.download_to_data())
            return d

        d.addErrback(_maybe_retry)
        return d


    def _record_size(self, mfv):
        """
        I record the size of a mutable file version.
        """
        self._most_recent_size = mfv.get_size()
        return mfv


    def get_size_of_best_version(self):
        """
        I return the size of the best version of this mutable file.

        This is equivalent to calling get_size() on the result of
        get_best_readable_version().
        """
        d = self.get_best_readable_version()
        return d.addCallback(lambda mfv: mfv.get_size())


    #################################
    # IMutableFileNode

    def get_best_mutable_version(self, servermap=None):
        """
        I return a Deferred that fires with a MutableFileVersion
        representing the best readable version of the file that I
        represent. I am like get_best_readable_version, except that I
        will try to make a writeable version if I can.
        """
        return self.get_mutable_version(servermap=servermap)


    def get_mutable_version(self, servermap=None, version=None):
        """
        I return a version of this mutable file. I return a Deferred
        that fires with a MutableFileVersion

        If version is provided, the Deferred will fire with a
        MutableFileVersion initailized with that version. Otherwise, it
        will fire with the best version that I can recover.

        If servermap is provided, I will use that to find versions
        instead of performing my own servermap update.
        """
        if self.is_readonly():
            return self.get_readable_version(servermap=servermap,
                                             version=version)

        # get_mutable_version => write intent, so we require that the
        # servermap is updated in MODE_WRITE
        d = self._get_version_from_servermap(MODE_WRITE, servermap, version)
        def _build_version((servermap, smap_version)):
            # these should have been set by the servermap update.
            assert self._secret_holder
            assert self._writekey

            mfv = MutableFileVersion(self,
                                     servermap,
                                     smap_version,
                                     self._storage_index,
                                     self._storage_broker,
                                     self._readkey,
                                     self._writekey,
                                     self._secret_holder,
                                     history=self._history)
            assert not mfv.is_readonly()
            mfv.set_downloader_hints(self._downloader_hints)
            return mfv

        return d.addCallback(_build_version)


    # XXX: I'm uncomfortable with the difference between upload and
    #      overwrite, which, FWICT, is basically that you don't have to
    #      do a servermap update before you overwrite. We split them up
    #      that way anyway, so I guess there's no real difficulty in
    #      offering both ways to callers, but it also makes the
    #      public-facing API cluttery, and makes it hard to discern the
    #      right way of doing things.

    # In general, we leave it to callers to ensure that they aren't
    # going to cause UncoordinatedWriteErrors when working with
    # MutableFileVersions. We know that the next three operations
    # (upload, overwrite, and modify) will all operate on the same
    # version, so we say that only one of them can be going on at once,
    # and serialize them to ensure that that actually happens, since as
    # the caller in this situation it is our job to do that.
    def overwrite(self, new_contents):
        """
        I overwrite the contents of the best recoverable version of this
        mutable file with new_contents. This is equivalent to calling
        overwrite on the result of get_best_mutable_version with
        new_contents as an argument. I return a Deferred that eventually
        fires with the results of my replacement process.
        """
        # TODO: Update downloader hints.
        return self._do_serialized(self._overwrite, new_contents)


    def _overwrite(self, new_contents):
        """
        I am the serialized sibling of overwrite.
        """
        d = self.get_best_mutable_version()
        d.addCallback(lambda mfv: mfv.overwrite(new_contents))
        d.addCallback(self._did_upload, new_contents.get_size())
        return d


    def upload(self, new_contents, servermap):
        """
        I overwrite the contents of the best recoverable version of this
        mutable file with new_contents, using servermap instead of
        creating/updating our own servermap. I return a Deferred that
        fires with the results of my upload.
        """
        # TODO: Update downloader hints
        return self._do_serialized(self._upload, new_contents, servermap)


    def modify(self, modifier, backoffer=None):
        """
        I modify the contents of the best recoverable version of this
        mutable file with the modifier. This is equivalent to calling
        modify on the result of get_best_mutable_version. I return a
        Deferred that eventually fires with an UploadResults instance
        describing this process.
        """
        # TODO: Update downloader hints.
        return self._do_serialized(self._modify, modifier, backoffer)


    def _modify(self, modifier, backoffer):
        """
        I am the serialized sibling of modify.
        """
        d = self.get_best_mutable_version()
        d.addCallback(lambda mfv: mfv.modify(modifier, backoffer))
        return d


    def download_version(self, servermap, version, fetch_privkey=False):
        """
        Download the specified version of this mutable file. I return a
        Deferred that fires with the contents of the specified version
        as a bytestring, or errbacks if the file is not recoverable.
        """
        d = self.get_readable_version(servermap, version)
        return d.addCallback(lambda mfv: mfv.download_to_data(fetch_privkey))


    def get_servermap(self, mode):
        """
        I return a servermap that has been updated in mode.

        mode should be one of MODE_READ, MODE_WRITE, MODE_CHECK or
        MODE_ANYTHING. See servermap.py for more on what these mean.
        """
        return self._do_serialized(self._get_servermap, mode)


    def _get_servermap(self, mode):
        """
        I am a serialized twin to get_servermap.
        """
        servermap = ServerMap()
        d = self._update_servermap(servermap, mode)
        # The servermap will tell us about the most recent size of the
        # file, so we may as well set that so that callers might get
        # more data about us.
        if not self._most_recent_size:
            d.addCallback(self._get_size_from_servermap)
        return d


    def _get_size_from_servermap(self, servermap):
        """
        I extract the size of the best version of this file and record
        it in self._most_recent_size. I return the servermap that I was
        given.
        """
        if servermap.recoverable_versions():
            v = servermap.best_recoverable_version()
            size = v[4] # verinfo[4] == size
            self._most_recent_size = size
        return servermap


    def _update_servermap(self, servermap, mode):
        u = ServermapUpdater(self, self._storage_broker, Monitor(), servermap,
                             mode)
        if self._history:
            self._history.notify_mapupdate(u.get_status())
        return u.update()


    #def set_version(self, version):
        # I can be set in two ways:
        #  1. When the node is created.
        #  2. (for an existing share) when the Servermap is updated 
        #     before I am read.
    #    assert version in (MDMF_VERSION, SDMF_VERSION)
    #    self._protocol_version = version


    def get_version(self):
        return self._protocol_version


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


    def _upload(self, new_contents, servermap):
        """
        A MutableFileNode still has to have some way of getting
        published initially, which is what I am here for. After that,
        all publishing, updating, modifying and so on happens through
        MutableFileVersions.
        """
        assert self._pubkey, "update_servermap must be called before publish"

        # Define IPublishInvoker with a set_downloader_hints method?
        # Then have the publisher call that method when it's done publishing?
        p = Publish(self, self._storage_broker, servermap)
        if self._history:
            self._history.notify_publish(p.get_status(),
                                         new_contents.get_size())
        d = p.publish(new_contents)
        d.addCallback(self._did_upload, new_contents.get_size())
        return d


    def set_downloader_hints(self, hints):
        self._downloader_hints = hints

    def _did_upload(self, res, size):
        self._most_recent_size = size
        return res


class MutableFileVersion:
    """
    I represent a specific version (most likely the best version) of a
    mutable file.

    Since I implement IReadable, instances which hold a
    reference to an instance of me are guaranteed the ability (absent
    connection difficulties or unrecoverable versions) to read the file
    that I represent. Depending on whether I was initialized with a
    write capability or not, I may also provide callers the ability to
    overwrite or modify the contents of the mutable file that I
    reference.
    """
    implements(IMutableFileVersion, IWriteable)

    def __init__(self,
                 node,
                 servermap,
                 version,
                 storage_index,
                 storage_broker,
                 readcap,
                 writekey=None,
                 write_secrets=None,
                 history=None):

        self._node = node
        self._servermap = servermap
        self._version = version
        self._storage_index = storage_index
        self._write_secrets = write_secrets
        self._history = history
        self._storage_broker = storage_broker

        #assert isinstance(readcap, IURI)
        self._readcap = readcap

        self._writekey = writekey
        self._serializer = defer.succeed(None)


    def get_sequence_number(self):
        """
        Get the sequence number of the mutable version that I represent.
        """
        return self._version[0] # verinfo[0] == the sequence number


    # TODO: Terminology?
    def get_writekey(self):
        """
        I return a writekey or None if I don't have a writekey.
        """
        return self._writekey


    def set_downloader_hints(self, hints):
        """
        I set the downloader hints.
        """
        assert isinstance(hints, dict)

        self._downloader_hints = hints


    def get_downloader_hints(self):
        """
        I return the downloader hints.
        """
        return self._downloader_hints


    def overwrite(self, new_contents):
        """
        I overwrite the contents of this mutable file version with the
        data in new_contents.
        """
        assert not self.is_readonly()

        return self._do_serialized(self._overwrite, new_contents)


    def _overwrite(self, new_contents):
        assert IMutableUploadable.providedBy(new_contents)
        assert self._servermap.last_update_mode == MODE_WRITE

        return self._upload(new_contents)


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
        assert not self.is_readonly()

        return self._do_serialized(self._modify, modifier, backoffer)


    def _modify(self, modifier, backoffer):
        if backoffer is None:
            backoffer = BackoffAgent().delay
        return self._modify_and_retry(modifier, backoffer, True)


    def _modify_and_retry(self, modifier, backoffer, first_time):
        """
        I try to apply modifier to the contents of this version of the
        mutable file. If I succeed, I return an UploadResults instance
        describing my success. If I fail, I try again after waiting for
        a little bit.
        """
        log.msg("doing modify")
        if first_time:
            d = self._update_servermap()
        else:
            # We ran into trouble; do MODE_CHECK so we're a little more
            # careful on subsequent tries.
            d = self._update_servermap(mode=MODE_CHECK)

        d.addCallback(lambda ignored:
            self._modify_once(modifier, first_time))
        def _retry(f):
            f.trap(UncoordinatedWriteError)
            # Uh oh, it broke. We're allowed to trust the servermap for our
            # first try, but after that we need to update it. It's
            # possible that we've failed due to a race with another
            # uploader, and if the race is to converge correctly, we
            # need to know about that upload.
            d2 = defer.maybeDeferred(backoffer, self, f)
            d2.addCallback(lambda ignored:
                           self._modify_and_retry(modifier,
                                                  backoffer, False))
            return d2
        d.addErrback(_retry)
        return d


    def _modify_once(self, modifier, first_time):
        """
        I attempt to apply a modifier to the contents of the mutable
        file.
        """
        assert self._servermap.last_update_mode != MODE_READ

        # download_to_data is serialized, so we have to call this to
        # avoid deadlock.
        d = self._try_to_download_data()
        def _apply(old_contents):
            new_contents = modifier(old_contents, self._servermap, first_time)
            precondition((isinstance(new_contents, str) or
                          new_contents is None),
                         "Modifier function must return a string "
                         "or None")

            if new_contents is None or new_contents == old_contents:
                log.msg("no changes")
                # no changes need to be made
                if first_time:
                    return
                # However, since Publish is not automatically doing a
                # recovery when it observes UCWE, we need to do a second
                # publish. See #551 for details. We'll basically loop until
                # we managed an uncontested publish.
                old_uploadable = MutableData(old_contents)
                new_contents = old_uploadable
            else:
                new_contents = MutableData(new_contents)

            return self._upload(new_contents)
        d.addCallback(_apply)
        return d


    def is_readonly(self):
        """
        I return True if this MutableFileVersion provides no write
        access to the file that it encapsulates, and False if it
        provides the ability to modify the file.
        """
        return self._writekey is None


    def is_mutable(self):
        """
        I return True, since mutable files are always mutable by
        somebody.
        """
        return True


    def get_storage_index(self):
        """
        I return the storage index of the reference that I encapsulate.
        """
        return self._storage_index


    def get_size(self):
        """
        I return the length, in bytes, of this readable object.
        """
        return self._servermap.size_of_version(self._version)


    def download_to_data(self, fetch_privkey=False):
        """
        I return a Deferred that fires with the contents of this
        readable object as a byte string.

        """
        c = consumer.MemoryConsumer()
        d = self.read(c, fetch_privkey=fetch_privkey)
        d.addCallback(lambda mc: "".join(mc.chunks))
        return d


    def _try_to_download_data(self):
        """
        I am an unserialized cousin of download_to_data; I am called
        from the children of modify() to download the data associated
        with this mutable version.
        """
        c = consumer.MemoryConsumer()
        # modify will almost certainly write, so we need the privkey.
        d = self._read(c, fetch_privkey=True)
        d.addCallback(lambda mc: "".join(mc.chunks))
        return d


    def read(self, consumer, offset=0, size=None, fetch_privkey=False):
        """
        I read a portion (possibly all) of the mutable file that I
        reference into consumer.
        """
        return self._do_serialized(self._read, consumer, offset, size,
                                   fetch_privkey)


    def _read(self, consumer, offset=0, size=None, fetch_privkey=False):
        """
        I am the serialized companion of read.
        """
        r = Retrieve(self._node, self._servermap, self._version, fetch_privkey)
        if self._history:
            self._history.notify_retrieve(r.get_status())
        d = r.download(consumer, offset, size)
        return d


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


    def _upload(self, new_contents):
        #assert self._pubkey, "update_servermap must be called before publish"
        p = Publish(self._node, self._storage_broker, self._servermap)
        if self._history:
            self._history.notify_publish(p.get_status(),
                                         new_contents.get_size())
        d = p.publish(new_contents)
        d.addCallback(self._did_upload, new_contents.get_size())
        return d


    def _did_upload(self, res, size):
        self._most_recent_size = size
        return res

    def update(self, data, offset):
        """
        Do an update of this mutable file version by inserting data at
        offset within the file. If offset is the EOF, this is an append
        operation. I return a Deferred that fires with the results of
        the update operation when it has completed.

        In cases where update does not append any data, or where it does
        not append so many blocks that the block count crosses a
        power-of-two boundary, this operation will use roughly
        O(data.get_size()) memory/bandwidth/CPU to perform the update.
        Otherwise, it must download, re-encode, and upload the entire
        file again, which will use O(filesize) resources.
        """
        return self._do_serialized(self._update, data, offset)


    def _update(self, data, offset):
        """
        I update the mutable file version represented by this particular
        IMutableVersion by inserting the data in data at the offset
        offset. I return a Deferred that fires when this has been
        completed.
        """
        new_size = data.get_size() + offset
        old_size = self.get_size()
        segment_size = self._version[3]
        num_old_segments = mathutil.div_ceil(old_size,
                                             segment_size)
        num_new_segments = mathutil.div_ceil(new_size,
                                             segment_size)
        log.msg("got %d old segments, %d new segments" % \
                        (num_old_segments, num_new_segments))

        # We do a whole file re-encode if the file is an SDMF file. 
        if self._version[2]: # version[2] == SDMF salt, which MDMF lacks
            log.msg("doing re-encode instead of in-place update")
            return self._do_modify_update(data, offset)

        # Otherwise, we can replace just the parts that are changing.
        log.msg("updating in place")
        d = self._do_update_update(data, offset)
        d.addCallback(self._decode_and_decrypt_segments, data, offset)
        d.addCallback(self._build_uploadable_and_finish, data, offset)
        return d


    def _do_modify_update(self, data, offset):
        """
        I perform a file update by modifying the contents of the file
        after downloading it, then reuploading it. I am less efficient
        than _do_update_update, but am necessary for certain updates.
        """
        def m(old, servermap, first_time):
            start = offset
            rest = offset + data.get_size()
            new = old[:start]
            new += "".join(data.read(data.get_size()))
            new += old[rest:]
            return new
        return self._modify(m, None)


    def _do_update_update(self, data, offset):
        """
        I start the Servermap update that gets us the data we need to
        continue the update process. I return a Deferred that fires when
        the servermap update is done.
        """
        assert IMutableUploadable.providedBy(data)
        assert self.is_mutable()
        # offset == self.get_size() is valid and means that we are
        # appending data to the file.
        assert offset <= self.get_size()

        segsize = self._version[3]
        # We'll need the segment that the data starts in, regardless of
        # what we'll do later.
        start_segment = offset // segsize

        # We only need the end segment if the data we append does not go
        # beyond the current end-of-file.
        end_segment = start_segment
        if offset + data.get_size() < self.get_size():
            end_data = offset + data.get_size()
            # The last byte we touch is the end_data'th byte, which is actually
            # byte end_data - 1 because bytes are zero-indexed.
            end_data -= 1
            end_segment = end_data // segsize

        self._start_segment = start_segment
        self._end_segment = end_segment

        # Now ask for the servermap to be updated in MODE_WRITE with
        # this update range.
        return self._update_servermap(update_range=(start_segment,
                                                    end_segment))


    def _decode_and_decrypt_segments(self, ignored, data, offset):
        """
        After the servermap update, I take the encrypted and encoded
        data that the servermap fetched while doing its update and
        transform it into decoded-and-decrypted plaintext that can be
        used by the new uploadable. I return a Deferred that fires with
        the segments.
        """
        r = Retrieve(self._node, self._servermap, self._version)
        # decode: takes in our blocks and salts from the servermap,
        # returns a Deferred that fires with the corresponding plaintext
        # segments. Does not download -- simply takes advantage of
        # existing infrastructure within the Retrieve class to avoid
        # duplicating code.
        sm = self._servermap
        # XXX: If the methods in the servermap don't work as
        # abstractions, you should rewrite them instead of going around
        # them.
        update_data = sm.update_data
        start_segments = {} # shnum -> start segment
        end_segments = {} # shnum -> end segment
        blockhashes = {} # shnum -> blockhash tree
        for (shnum, original_data) in update_data.iteritems():
            data = [d[1] for d in original_data if d[0] == self._version]

            # Every data entry in our list should now be share shnum for
            # a particular version of the mutable file, so all of the
            # entries should be identical.
            datum = data[0]
            assert [x for x in data if x != datum] == []

            blockhashes[shnum] = datum[0]
            start_segments[shnum] = datum[1]
            end_segments[shnum] = datum[2]

        d1 = r.decode(start_segments, self._start_segment)
        d2 = r.decode(end_segments, self._end_segment)
        d3 = defer.succeed(blockhashes)
        return deferredutil.gatherResults([d1, d2, d3])


    def _build_uploadable_and_finish(self, segments_and_bht, data, offset):
        """
        After the process has the plaintext segments, I build the
        TransformingUploadable that the publisher will eventually
        re-upload to the grid. I then invoke the publisher with that
        uploadable, and return a Deferred when the publish operation has
        completed without issue.
        """
        u = TransformingUploadable(data, offset,
                                   self._version[3],
                                   segments_and_bht[0],
                                   segments_and_bht[1])
        p = Publish(self._node, self._storage_broker, self._servermap)
        return p.update(u, offset, segments_and_bht[2], self._version)


    def _update_servermap(self, mode=MODE_WRITE, update_range=None):
        """
        I update the servermap. I return a Deferred that fires when the
        servermap update is done.
        """
        if update_range:
            u = ServermapUpdater(self._node, self._storage_broker, Monitor(),
                                 self._servermap,
                                 mode=mode,
                                 update_range=update_range)
        else:
            u = ServermapUpdater(self._node, self._storage_broker, Monitor(),
                                 self._servermap,
                                 mode=mode)
        return u.update()
