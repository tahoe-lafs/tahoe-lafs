
import struct
from zope.interface import implements
from twisted.internet import defer
from allmydata.interfaces import IMutableFileNode, IMutableFileURI
from allmydata.util import hashutil
from allmydata.uri import WriteableSSKFileURI

class MutableFileNode:
    implements(IMutableFileNode)

    def __init__(self, client):
        self._client = client
        self._pubkey = None # filled in upon first read
        self._privkey = None # filled in if we're mutable
        self._sharemap = {} # known shares, shnum-to-nodeid

    def init_from_uri(self, myuri):
        self._uri = IMutableFileURI(myuri)
        return self

    def create(self, initial_contents):
        """Call this when the filenode is first created. This will generate
        the keys, generate the initial shares, allocate shares, and upload
        the initial contents. Returns a Deferred that fires (with the
        MutableFileNode instance you should use) when it completes.
        """
        self._privkey = "very private"
        self._pubkey = "public"
        self._writekey = hashutil.ssk_writekey_hash(self._privkey)
        self._fingerprint = hashutil.ssk_pubkey_fingerprint_hash(self._pubkey)
        self._uri = WriteableSSKFileURI(self._writekey, self._fingerprint)
        d = defer.succeed(None)
        return d


    def get_uri(self):
        return self._uri.to_string()

    def is_mutable(self):
        return self._uri.is_mutable()

    def __hash__(self):
        return hash((self.__class__, self.uri))
    def __cmp__(self, them):
        if cmp(type(self), type(them)):
            return cmp(type(self), type(them))
        if cmp(self.__class__, them.__class__):
            return cmp(self.__class__, them.__class__)
        return cmp(self.uri, them.uri)

    def get_verifier(self):
        return IMutableFileURI(self._uri).get_verifier()

    def check(self):
        verifier = self.get_verifier()
        return self._client.getServiceNamed("checker").check(verifier)

    def download(self, target):
        #downloader = self._client.getServiceNamed("downloader")
        #return downloader.download(self.uri, target)
        raise NotImplementedError

    def download_to_data(self):
        #downloader = self._client.getServiceNamed("downloader")
        #return downloader.download_to_data(self.uri)
        return defer.succeed("this isn't going to fool you, is it")

    def replace(self, newdata):
        return defer.succeed(None)

    def unpack_data(self, data):
        offsets = {}
        (version,
         seqnum,
         root_hash,
         k, N, segsize, datalen,
         offsets['signature'],
         offsets['share_hash_chain'],
         offsets['block_hash_tree'],
         offsets['IV'],
         offsets['share_data'],
         offsets['enc_privkey']) = struct.unpack(">BQ32s" + "BBQQ" + "LLLLLQ")
        assert version == 0
        signature = data[offsets['signature']:offsets['share_hash_chain']]
        share_hash_chain = data[offsets['share_hash_chain']:offsets['block_hash_tree']]
        block_hash_tree = data[offsets['block_hash_tree']:offsets['IV']]
        IV = data[offsets['IV']:offsets['share_data']]
        share_data = data[offsets['share_data']:offsets['share_data']+datalen]
        enc_privkey = data[offsets['enc_privkey']:]

    def pack_data(self):
        # dummy values to satisfy pyflakes until we wire this all up
        seqnum, root_hash, k, N, segsize, datalen = 0,0,0,0,0,0
        (verification_key, signature, share_hash_chain, block_hash_tree,
         IV, share_data, enc_privkey) = ["0"*16] * 7
        seqnum += 1
        newbuf = [struct.pack(">BQ32s" + "BBQQ",
                              0, # version byte
                              seqnum,
                              root_hash,
                              k, N, segsize, datalen)]
        post_offset = struct.calcsize(">BQ32s" + "BBQQ" + "LLLLLQ")
        offsets = {}
        o1 = offsets['signature'] = post_offset + len(verification_key)
        o2 = offsets['share_hash_chain'] = o1 + len(signature)
        o3 = offsets['block_hash_tree'] = o2 + len(share_hash_chain)
        assert len(IV) == 16
        o4 = offsets['IV'] = o3 + len(block_hash_tree)
        o5 = offsets['share_data'] = o4 + len(IV)
        o6 = offsets['enc_privkey'] = o5 + len(share_data)

        newbuf.append(struct.pack(">LLLLLQ",
                                  offsets['signature'],
                                  offsets['share_hash_chain'],
                                  offsets['block_hash_tree'],
                                  offsets['IV'],
                                  offsets['share_data'],
                                  offsets['enc_privkey']))
        newbuf.extend([verification_key,
                       signature,
                       share_hash_chain,
                       block_hash_tree,
                       IV,
                       share_data,
                       enc_privkey])
        return "".join(newbuf)


# use client.create_mutable_file() to make one of these
