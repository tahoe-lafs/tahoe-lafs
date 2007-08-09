from zfec import filefec

import os

from pyutil import benchutil

FNAME="benchrandom.data"

def _make_new_rand_file(size):
    open(FNAME, "wb").write(os.urandom(size))

def donothing(results, reslenthing):
    pass

import sha
hashers = [ sha.new() for i in range(100) ]
def hashem(results, reslenthing):
    for i, result in enumerate(results):
        hashers[i].update(result)

def _encode_file(N):
    filefec.encode_file(open(FNAME, "rb"), donothing, 25, 100)
   
def _encode_file_stringy(N):
    filefec.encode_file_stringy(open(FNAME, "rb"), donothing, 25, 100)
   
def _encode_file_stringy_easyfec(N):
    filefec.encode_file_stringy_easyfec(open(FNAME, "rb"), donothing, 25, 100)

def _encode_file_not_really(N):
    filefec.encode_file_not_really(open(FNAME, "rb"), donothing, 25, 100)

def _encode_file_not_really_and_hash(N):
    filefec.encode_file_not_really_and_hash(open(FNAME, "rb"), donothing, 25, 100)

def _encode_file_and_hash(N):
    filefec.encode_file(open(FNAME, "rb"), hashem, 25, 100)

def bench():
    # for f in [_encode_file_stringy_easyfec, _encode_file_stringy, _encode_file, _encode_file_not_really,]:
    # for f in [_encode_file,]:
    for f in [_encode_file_not_really, _encode_file_not_really_and_hash, _encode_file, _encode_file_and_hash,]:
        print f
        benchutil.bench(f, initfunc=_make_new_rand_file, TOPXP=23, MAXREPS=128, MAXTIME=64)

# bench()
