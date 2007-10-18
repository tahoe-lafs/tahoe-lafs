#! /usr/bin/python

# This is a munin plugin to track the average number of shares per file in
# each node's StorageServer. If this number is 100, that suggests there is
# only a single node in the entire mesh. If the number is 2, that suggests
# that there are 50 nodes in the entire mesh. If the number is 1, that
# suggests that there are >= 100 nodes in the entire mesh. (if there were a
# million nodes, this one node would only see a single share per file, so the
# number of shares-per-file will never be less than 1).

# Copy this plugin into /etc/munun/plugins/tahoe-sharesperfile and then put
# the following in your /etc/munin/plugin-conf.d/foo file to let it know
# where to find the basedirectory for each node:
#
#  [tahoe-sharesperfile]
#  env.basedir_NODE1 /path/to/node1
#  env.basedir_NODE2 /path/to/node2
#  env.basedir_NODE3 /path/to/node3
#

import os, sys

nodedirs = []
for k,v in os.environ.items():
    if k.startswith("basedir_"):
        nodename = k[len("basedir_"):]
        nodedirs.append( (nodename, v) )
nodedirs.sort()

configinfo = \
"""graph_title Allmydata Tahoe Shares Per File
graph_vlabel shares per file
graph_category tahoe
graph_info This graph shows the number of shares present for each file hosted by this node's StorageServer
"""
for nodename, basedir in nodedirs:
    configinfo += "%s.label %s\n" % (nodename, nodename)
    configinfo += "%s.draw LINE2\n" % (nodename,)


if len(sys.argv) > 1:
    if sys.argv[1] == "config":
        print configinfo
        sys.exit(0)

for nodename, basedir in nodedirs:
    files = 0
    shares = 0
    for f in os.listdir(os.path.join(basedir, "storage", "shares")):
        if f == "incoming":
            continue
        files += 1
        filedir = os.path.join(basedir, "storage", "shares", f)
        shares += len(os.listdir(filedir))
    if files:
        shares_per_file = 1.0 * shares / files
    else:
        shares_per_file = 0.0
    print "%s.value %.1f" % (nodename, shares_per_file)

