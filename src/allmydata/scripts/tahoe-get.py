#!/usr/bin/env python

import optparse, sys, urllib

def GET(url):
    f = urllib.urlopen(url)
    sys.stdout.write(f.read())

parser = optparse.OptionParser()
parser.add_option("-d", "--vdrive", dest="vdrive", default="global")
parser.add_option("-s", "--server", dest="server", default="http://tahoebs1.allmydata.com:8011")

(options, args) = parser.parse_args()

vfname = args[0]

base = "http://tahoebs1.allmydata.com:8011/"
base += "vdrive/"
base += options.vdrive
base += "/"

url = base + vfname

GET(url)
