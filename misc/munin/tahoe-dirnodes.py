#! /usr/bin/python

# This is a munin plugin to track the number of directory nodes that a vdrive
# server is maintaining on behalf of other nodes. If a mesh has only one
# vdrive server (or if clients are only bothering to use a single one), then
# this will be equal to the number of dirnodes in use in the entire mesh.

# Copy this plugin into /etc/munun/plugins/tahoe-dirnodes and then put
# the following in your /etc/munin/plugin-conf.d/foo file to let it know
# where to find the basedirectory for the vdrive server.
#
#  [tahoe-dirnodes]
#  env.basedir /path/to/vdrivenode

import os, sys

nodedir = os.environ["basedir"]

configinfo = \
"""graph_title Allmydata Tahoe Dirnode Count
graph_vlabel dirnodes
graph_category tahoe
graph_info This graph shows the number of directory nodes hosted by this vdrive server
dirnodes.label dirnodes
dirnodes.draw LINE2
"""


if len(sys.argv) > 1:
    if sys.argv[1] == "config":
        print configinfo.rstrip()
        sys.exit(0)

dirnodes = len(os.listdir(os.path.join(nodedir, "vdrive")))
if os.path.exists(os.path.join(nodedir, "vdrive", "root")):
    dirnodes -= 1 # the 'root' pointer doesn't count
print "dirnodes.value %d" % dirnodes

