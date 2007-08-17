#! /usr/bin/python

import urllib
import simplejson

def list(nodeurl, vdrive, vdrive_file):
    if nodeurl[-1] != "/":
        nodeurl += "/"
    url = nodeurl + "vdrive/" + vdrive + "/"
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
    import optparse, re
    parser = optparse.OptionParser()
    parser.add_option("-d", "--vdrive", dest="vdrive", default="global")
    parser.add_option("-u", "--node-url", dest="nodeurl")

    (options, args) = parser.parse_args()

    NODEURL_RE=re.compile("http://([^:]*)(:([1-9][0-9]*))?")
    if not isinstance(options.nodeurl, basestring) or not NODEURL_RE.match(options.nodeurl):
        raise ValueError("--node-url is required to be a string and look like \"http://HOSTNAMEORADDR:PORT\", not: %r" % (options.nodeurl,))
    
    vdrive_file = ""
    if args:
        vdrive_file = args[0]

    list(options.nodeurl, options.vdrive, vdrive_file)

if __name__ == '__main__':
    main()
