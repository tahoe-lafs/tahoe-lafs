#! /usr/bin/python

import urllib
import simplejson

def list(server, vdrive, vdrive_file):

    if server[-1] != "/":
        server += "/"
    url = server + "vdrive/" + vdrive + "/"
    if vdrive_file:
        url += vdrive_file
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



def main():
    import optparse
    parser = optparse.OptionParser()
    parser.add_option("-d", "--vdrive", dest="vdrive", default="global")
    parser.add_option("-s", "--server", dest="server", default="http://tahoebs1.allmydata.com:8011")

    (options, args) = parser.parse_args()

    vdrive_file = ""
    if args:
        vdrive_file = args[0]

    list(options.server, options.vdrive, vdrive_file)

if __name__ == '__main__':
    main()
