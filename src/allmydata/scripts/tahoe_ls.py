#! /usr/bin/python

import urllib
import simplejson

def list(nodeurl, root_uri, vdrive_pathname, stdout, stderr):
    if nodeurl[-1] != "/":
        nodeurl += "/"
    url = nodeurl + "uri/%s/" % urllib.quote(root_uri.replace("/","!"))
    if vdrive_pathname:
        url += urllib.quote(vdrive_pathname)
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
                print >>stdout, "%10s %s/" % ("", name)
            else:
                assert childtype == "filenode"
                size = child[1]['size']
                print >>stdout, "%10s %s" % (size, name)



def main():
    import optparse, re
    parser = optparse.OptionParser()
    parser.add_option("-u", "--node-url", dest="nodeurl")
    parser.add_option("-r", "--root-uri", dest="rooturi")

    (options, args) = parser.parse_args()

    NODEURL_RE=re.compile("http://([^:]*)(:([1-9][0-9]*))?")
    if not isinstance(options.nodeurl, basestring) or not NODEURL_RE.match(options.nodeurl):
        raise ValueError("--node-url is required to be a string and look like \"http://HOSTNAMEORADDR:PORT\", not: %r" % (options.nodeurl,))

    if not options.rooturi:
        raise ValueError("must provide --root-uri")

    vdrive_pathname = ""
    if args:
        vdrive_pathname = args[0]

    list(options.nodeurl, options.rooturi, vdrive_pathname)

if __name__ == '__main__':
    main()
