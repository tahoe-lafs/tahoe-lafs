#! /usr/bin/python

# ./check-umids.py src

import sys, re, os

ok = True
umids = {}

for starting_point in sys.argv[1:]:
    for root, dirs, files in os.walk(starting_point):
        for fn in [f for f in files if f.endswith(".py")]:
            fn = os.path.join(root, fn)
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
