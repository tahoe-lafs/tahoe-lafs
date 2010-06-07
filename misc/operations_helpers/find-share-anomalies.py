#!/usr/bin/env python

# feed this the results of 'tahoe catalog-shares' for all servers

import sys

chk_encodings = {}
sdmf_encodings = {}
sdmf_versions = {}

for catalog in sys.argv[1:]:
    for line in open(catalog, "r").readlines():
        line = line.strip()
        pieces = line.split()
        if pieces[0] == "CHK":
            ftype, si, kN, size, ueb_hash, expiration, filename = pieces
            if si not in chk_encodings:
                chk_encodings[si] = (set(), set())
            chk_encodings[si][0].add( (si, kN) )
            chk_encodings[si][1].add( line )
        if pieces[0] == "SDMF":
            ftype, si, kN, size, ver, expiration, filename = pieces
            if si not in sdmf_encodings:
                sdmf_encodings[si] = (set(), set())
            sdmf_encodings[si][0].add( (si, kN) )
            sdmf_encodings[si][1].add( line )
            if si not in sdmf_versions:
                sdmf_versions[si] = (set(), set())
            sdmf_versions[si][0].add( ver )
            sdmf_versions[si][1].add( line )

chk_multiple_encodings = [(si,lines)
                          for si,(encodings,lines) in chk_encodings.items()
                          if len(encodings) > 1]
chk_multiple_encodings.sort()
sdmf_multiple_encodings = [(si,lines)
                           for si,(encodings,lines) in sdmf_encodings.items()
                           if len(encodings) > 1
                           ]
sdmf_multiple_encodings.sort()
sdmf_multiple_versions = [(si,lines)
                          for si,(versions,lines) in sdmf_versions.items()
                          if len(versions) > 1]
sdmf_multiple_versions.sort()

if chk_multiple_encodings:
    print
    print "CHK multiple encodings:"
    for (si,lines) in chk_multiple_encodings:
        print " " + si
        for line in sorted(lines):
            print "  " + line
if sdmf_multiple_encodings:
    print
    print "SDMF multiple encodings:"
    for (si,lines) in sdmf_multiple_encodings:
        print " " + si
        for line in sorted(lines):
            print "  " + line
if sdmf_multiple_versions:
    print
    print "SDMF multiple versions:"
    for (si,lines) in sdmf_multiple_versions:
        print " " + si
        for line in sorted(lines):
            print "  " + line
