"""
CRS encoding and decoding.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from zope.interface import implementer
from allmydata.util import mathutil
from allmydata.util.assertutil import precondition
from allmydata.util.cputhreadpool import defer_to_thread
from allmydata.util.deferredutil import async_to_deferred
from allmydata.interfaces import ICodecEncoder, ICodecDecoder
import zfec

@implementer(ICodecEncoder)
class CRSEncoder(object):
    ENCODER_TYPE = b"crs"

    def set_params(self, data_size, required_shares, max_shares):
        assert required_shares <= max_shares
        self.data_size = data_size
        self.required_shares = required_shares
        self.max_shares = max_shares
        self.share_size = mathutil.div_ceil(data_size, required_shares)
        self.last_share_padding = mathutil.pad_size(self.share_size, required_shares)
        self.encoder = zfec.Encoder(required_shares, max_shares)

    def get_encoder_type(self):
        return self.ENCODER_TYPE

    def get_params(self):
        return (self.data_size, self.required_shares, self.max_shares)

    def get_serialized_params(self):
        return b"%d-%d-%d" % (self.data_size, self.required_shares,
                              self.max_shares)

    def get_block_size(self):
        return self.share_size

    @async_to_deferred
    async def encode(self, inshares, desired_share_ids=None):
        precondition(desired_share_ids is None or len(desired_share_ids) <= self.max_shares, desired_share_ids, self.max_shares)

        if desired_share_ids is None:
            desired_share_ids = list(range(self.max_shares))

        for inshare in inshares:
            assert len(inshare) == self.share_size, (len(inshare), self.share_size, self.data_size, self.required_shares)
        shares = await defer_to_thread(self.encoder.encode, inshares, desired_share_ids)
        return (shares, desired_share_ids)

    def encode_proposal(self, data, desired_share_ids=None):
        raise NotImplementedError()


@implementer(ICodecDecoder)
class CRSDecoder(object):

    def set_params(self, data_size, required_shares, max_shares):
        self.data_size = data_size
        self.required_shares = required_shares
        self.max_shares = max_shares

        self.chunk_size = self.required_shares
        self.num_chunks = mathutil.div_ceil(self.data_size, self.chunk_size)
        self.share_size = self.num_chunks
        self.decoder = zfec.Decoder(self.required_shares, self.max_shares)

    def get_needed_shares(self):
        return self.required_shares

    @async_to_deferred
    async def decode(self, some_shares, their_shareids):
        precondition(len(some_shares) == len(their_shareids),
                     len(some_shares), len(their_shareids))
        precondition(len(some_shares) == self.required_shares,
                     len(some_shares), self.required_shares)
        return await defer_to_thread(
            self.decoder.decode,
            some_shares,
            [int(s) for s in their_shareids]
        )

def parse_params(serializedparams):
    pieces = serializedparams.split(b"-")
    return int(pieces[0]), int(pieces[1]), int(pieces[2])
