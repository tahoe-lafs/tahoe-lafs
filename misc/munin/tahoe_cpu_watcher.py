#! /usr/bin/python

import os, sys, re
import urllib
import simplejson

url = os.environ["url"]
current = simplejson.loads(urllib.urlopen(url).read())

configinfo = """\
graph_title Tahoe CPU Usage
graph_vlabel CPU %
graph_category tahoe
graph_info This graph shows the 5min average of CPU usage for each process
"""
data = ""

for (name, avg1, avg5, avg15) in current:
    dataname = re.sub(r'[^\w]', '_', name)
    configinfo += dataname + ".label " + name + "\n"
    configinfo += dataname + ".draw LINE2\n"
    if avg5 is not None:
        data += dataname + ".value %.2f\n" % (100.0 * avg5)

if len(sys.argv) > 1:
    if sys.argv[1] == "config":
        print configinfo.rstrip()
        sys.exit(0)
print data.rstrip()
