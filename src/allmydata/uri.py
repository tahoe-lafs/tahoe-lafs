
from allmydata.util import idlib

# the URI shall be an ascii representation of the file. It shall contain
# enough information to retrieve and validate the contents. It shall be
# expressed in a limited character set (namely [TODO]).

def pack_uri(codec_name, codec_params, verifierid, roothash, needed_shares, total_shares, size, segment_size):
    assert isinstance(codec_name, str)
    assert len(codec_name) < 10
    assert ":" not in codec_name
    assert isinstance(codec_params, str)
    assert ":" not in codec_params
    assert isinstance(verifierid, str)
    assert len(verifierid) == 20 # sha1 hash
    return "URI:%s:%s:%s:%s:%s:%s:%s:%s" % (codec_name, codec_params, idlib.b2a(verifierid), idlib.b2a(roothash), needed_shares, total_shares, size, segment_size)


def unpack_uri(uri):
    assert uri.startswith("URI:")
    header, codec_name, codec_params, verifierid_s, roothash_s, needed_shares_s, total_shares_s, size_s, segment_size_s = uri.split(":")
    verifierid = idlib.a2b(verifierid_s)
    roothash = idlib.a2b(roothash_s)
    needed_shares = int(needed_shares_s)
    total_shares = int(total_shares_s)
    size = int(size_s)
    segment_size = int(segment_size_s)
    return codec_name, codec_params, verifierid, roothash, needed_shares, total_shares, size, segment_size


