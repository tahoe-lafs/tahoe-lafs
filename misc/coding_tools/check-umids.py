#! /usr/bin/python
# -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-

# ./rumid.py foo.py

import sys, re, os

ok = True
umids = {}

for fn in sys.argv[1:]:
    fn = os.path.abspath(fn)
    for lineno,line in enumerate(open(fn, "r").readlines()):
        lineno = lineno+1
        if "umid" not in line:
            continue
        mo = re.search("umid=[\"\']([^\"\']+)[\"\']", line)
        if mo:
            umid = mo.group(1)
            if umid in umids:
                oldfn, oldlineno = umids[umid]
                print "%s:%d: duplicate umid '%s'" % (fn, lineno, umid)
                print "%s:%d: first used here" % (oldfn, oldlineno)
                ok = False
            umids[umid] = (fn,lineno)

if ok:
    print "all umids are unique"
else:
    print "some umids were duplicates"
    sys.exit(1)
