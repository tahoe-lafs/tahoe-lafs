#! /usr/bin/python

import optparse, sys, urllib
import simplejson

def GET(url, outf):
    f = urllib.urlopen(url)
    outf.write(f.read())

parser = optparse.OptionParser()
parser.add_option("-d", "--vdrive", dest="vdrive", default="global")
parser.add_option("-s", "--server", dest="server", default="http://tahoebs1.allmydata.com:8011")

(options, args) = parser.parse_args()


url = options.server + "/vdrive/" + options.vdrive
if args:
    url += "/" + args[0]
url += "?t=json"
data = urllib.urlopen(url).read()

parsed = simplejson.loads(data)
nodetype, d = parsed
if nodetype == "dirnode":
    childnames = sorted(d['children'].keys())
    for name in childnames:
        child = d['children'][name]
        childtype = child[0]
        if childtype == "dirnode":
            print "%10s %s/" % ("", name)
        else:
            assert childtype == "filenode"
            size = child[1]['size']
            print "%10s %s" % (size, name)


