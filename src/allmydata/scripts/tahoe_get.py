#!/usr/bin/env python

import sys, urllib

def get(server, vdrive, vdrive_file, local_file):

    if server[-1] != "/":
        server += "/"
    url = server + "vdrive/" + vdrive + "/"
    if vdrive_file:
        url += vdrive_file

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
    import optparse
    parser = optparse.OptionParser()
    parser.add_option("-d", "--vdrive", dest="vdrive", default="global")
    parser.add_option("-s", "--server", dest="server", default="http://tahoebs1.allmydata.com:8011")

    (options, args) = parser.parse_args()

    vdrive_file = args[0]
    local_file = None
    if len(args) > 1:
        local_file = args[1]

    get(options.server, options.vdrive, vdrive_file, local_file)

if __name__ == '__main__':
    main()
