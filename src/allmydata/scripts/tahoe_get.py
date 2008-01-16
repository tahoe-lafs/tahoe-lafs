
import urllib

def get(nodeurl, dir_uri, vdrive_fname, local_file, stdout, stderr):
    if nodeurl[-1] != "/":
        nodeurl += "/"
    url = nodeurl + "uri/%s/" % urllib.quote(dir_uri)
    if vdrive_fname:
        url += urllib.quote(vdrive_fname)

    if local_file is None or local_file == "-":
        outf = stdout
        close_outf = False
    else:
        outf = open(local_file, "wb")
        close_outf = True
    inf = urllib.urlopen(url)
    while True:
        data = inf.read(4096)
        if not data:
            break
        outf.write(data)
    if close_outf:
        outf.close()

    return 0
