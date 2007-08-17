#!/usr/bin/env python

import sys, urllib

def get(nodeurl, vdrive, vdrive_fname, local_file):
    if nodeurl[-1] != "/":
        nodeurl += "/"
    url = nodeurl + "vdrive/" + vdrive + "/"
    if vdrive_fname:
        url += vdrive_fname

    if local_file is None or local_file == "-":
        outf = sys.stdout
    else:
        outf = open(local_file, "wb")
    inf = urllib.urlopen(url)
    while True:
        data = inf.read(4096)
        if not data:
            break
        outf.write(data)
    outf.close()

    return 0


def main():
    import optparse, re
    parser = optparse.OptionParser()
    parser.add_option("-d", "--vdrive", dest="vdrive", default="global")
    parser.add_option("-u", "--nodeurl", dest="nodeurl")

    (options, args) = parser.parse_args()

    NODEURL_RE=re.compile("http://([^:]*)(:([1-9][0-9]*))?")
    if not isinstance(options.nodeurl, basestring) or not NODEURL_RE.match(options.nodeurl):
        raise ValueError("--node-url is required to be a string and look like \"http://HOSTNAMEORADDR:PORT\", not: %r" % (options.nodeurl,))
    
    vdrive_fname = args[0]
    local_file = None
    if len(args) > 1:
        local_file = args[1]

    get(options.nodeurl, options.vdrive, vdrive_fname, local_file)

if __name__ == '__main__':
    main()
