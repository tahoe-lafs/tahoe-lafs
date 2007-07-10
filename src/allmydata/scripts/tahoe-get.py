#!/usr/bin/env python

import optparse, sys, urllib

def GET(url, outf):
    f = urllib.urlopen(url)
    outf.write(f.read())

parser = optparse.OptionParser()
parser.add_option("-d", "--vdrive", dest="vdrive", default="global")
parser.add_option("-s", "--server", dest="server", default="http://tahoebs1.allmydata.com:8011")

(options, args) = parser.parse_args()

vfname = args[0]
if len(args) == 1 or args[1] == "-":
    targfname = None
else:
    targfname = args[1]

base = options.server
base += "/vdrive/"
base += options.vdrive
base += "/"

url = base + vfname

if targfname is None:
    GET(url, sys.stdout)
else:
    GET(url, open(targfname, "wb"))
