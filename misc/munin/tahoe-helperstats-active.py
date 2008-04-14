#! /usr/bin/python

import os, sys
import urllib
import simplejson

configinfo = """\
graph_title Tahoe Helper Stats - Active Files
graph_vlabel bytes
graph_category tahoe
graph_info This graph shows the number of files being actively processed by the helper
fetched.label Active Files
fetched.draw LINE2
"""

if len(sys.argv) > 1:
    if sys.argv[1] == "config":
        print configinfo.rstrip()
        sys.exit(0)

url = os.environ["url"]

data = simplejson.loads(urllib.urlopen(url).read())
print "fetched.value %d" % data["chk_upload_helper.active_uploads"]

