#! /usr/bin/python

from allmydata import __version__ as v

import sys

if len(sys.argv) == 1:
    input = sys.stdin
elif len(sys.argv) == 2:
    fname = sys.argv[1]
    input = file(fname, 'rb')
else:
    raise ValueError('must provide 0 or 1 argument (stdin, or filename)')

vern = { 
    'major': v.major or 0,
    'minor': v.minor or 0,
    'point': v.micro or 0,
    'micro': v.micro or 0,
    'revision' : v.revision or 0,
    'build': str(v),
    }

for line in input.readlines():
    print line % vern,

