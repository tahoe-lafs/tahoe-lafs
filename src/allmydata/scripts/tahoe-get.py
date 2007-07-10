#!/usr/bin/env python

import sys, urllib

def GET(url):
    f = urllib.urlopen(url)
    sys.stdout.write(f.read())

vfname = sys.argv[1]

base = "http://tahoebs1.allmydata.com:8011/"
base += "vdrive/global/"
url = base + vfname

GET(url)
