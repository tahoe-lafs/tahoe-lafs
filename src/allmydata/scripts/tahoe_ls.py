
import urllib
import simplejson

def list(nodeurl, dir_uri, vdrive_pathname, stdout, stderr):
    if nodeurl[-1] != "/":
        nodeurl += "/"
    url = nodeurl + "uri/%s/" % urllib.quote(dir_uri)
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
    elif nodetype == "filenode":
        print >>stdout, "%10s %s" % (d['size'], vdrive_pathname)
