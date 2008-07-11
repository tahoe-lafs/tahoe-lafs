#! /usr/bin/python

import os, sys
import urllib
import simplejson

configinfo = """\
graph_title Tahoe Helper Stats - Bytes Fetched
graph_vlabel bytes
graph_category tahoe
graph_info This graph shows the amount of data being fetched by the helper
fetched.label Bytes Fetched
fetched.type GAUGE
fetched.draw LINE1
fetched.min 0
"""

if len(sys.argv) > 1:
    if sys.argv[1] == "config":
        print configinfo.rstrip()
        sys.exit(0)

url = os.environ["url"]

data = simplejson.loads(urllib.urlopen(url).read())
print "fetched.value %d" % data["chk_upload_helper.fetched_bytes"]
