
from allmydata.util import idlib

# the URI shall be an ascii representation of the file. It shall contain
# enough information to retrieve and validate the contents. It shall be
# expressed in a limited character set (namely [TODO]).

def pack_uri(codec_name, codec_params, tail_codec_params,
             verifierid, fileid, key,
             roothash, needed_shares, total_shares, size, segment_size):
    # applications should pass keyword parameters into this
    assert isinstance(codec_name, str)
    assert len(codec_name) < 10
    assert ":" not in codec_name
    assert isinstance(codec_params, str)
    assert ":" not in codec_params
    assert isinstance(tail_codec_params, str)
    assert ":" not in tail_codec_params
    assert isinstance(verifierid, str)
    assert len(verifierid) == 20 # sha1 hash
    assert isinstance(fileid, str)
    assert len(fileid) == 20 # sha1 hash
    assert isinstance(key, str)
    assert len(key) == 16 # AES-128
    return "URI:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s" % (codec_name, codec_params, tail_codec_params, idlib.b2a(verifierid), idlib.b2a(fileid), idlib.b2a(key), idlib.b2a(roothash), needed_shares, total_shares, size, segment_size)


def unpack_uri(uri):
    assert uri.startswith("URI:")
    d = {}
    header, d['codec_name'], d['codec_params'], d['tail_codec_params'], verifierid_s, fileid_s, key_s, roothash_s, needed_shares_s, total_shares_s, size_s, segment_size_s = uri.split(":")
    assert header == "URI"
    d['verifierid'] = idlib.a2b(verifierid_s)
    d['fileid'] = idlib.a2b(fileid_s)
    d['key'] = idlib.a2b(key_s)
    d['roothash'] = idlib.a2b(roothash_s)
    d['needed_shares'] = int(needed_shares_s)
    d['total_shares'] = int(total_shares_s)
    d['size'] = int(size_s)
    d['segment_size'] = int(segment_size_s)
    return d


