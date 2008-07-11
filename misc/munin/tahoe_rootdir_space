#! /usr/bin/python

import os, sys
import urllib
import simplejson

configinfo = """\
graph_title Tahoe Root Directory Size
graph_vlabel bytes
graph_category tahoe
graph_info This graph shows the amount of space consumed by all files reachable from a given directory
space.label Space
space.draw LINE2
"""

if len(sys.argv) > 1:
    if sys.argv[1] == "config":
        print configinfo.rstrip()
        sys.exit(0)

url = os.environ["url"]

data = int(urllib.urlopen(url).read().strip())
print "space.value %d" % data



