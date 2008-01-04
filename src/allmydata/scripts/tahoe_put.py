#!/usr/bin/env python

import urllib
from allmydata.scripts.common_http import do_http

def put(nodeurl, dir_uri, local_fname, vdrive_fname, verbosity,
        stdout, stderr):
    """
    @param verbosity: 0, 1, or 2, meaning quiet, verbose, or very verbose

    @return: a Deferred which eventually fires with the exit code
    """
    if nodeurl[-1] != "/":
        nodeurl += "/"
    url = nodeurl + "uri/%s/" % urllib.quote(dir_uri)
    if vdrive_fname:
        url += urllib.quote(vdrive_fname)

    infileobj = open(local_fname, "rb")
    resp = do_http("PUT", url, infileobj)

    if resp.status in (200, 201,):
        print >>stdout, "%s %s" % (resp.status, resp.reason)
        return 0

    print >>stderr, "error, got %s %s" % (resp.status, resp.reason)
    print >>stderr, resp.read()
    return 1

def main():
    import optparse, re
    parser = optparse.OptionParser()
    parser.add_option("-u", "--node-url", dest="nodeurl")
    parser.add_option("-r", "--dir-uri", dest="rooturi")

    (options, args) = parser.parse_args()

    NODEURL_RE=re.compile("http://([^:]*)(:([1-9][0-9]*))?")
    if not isinstance(options.nodeurl, basestring) or not NODEURL_RE.match(options.nodeurl):
        raise ValueError("--node-url is required to be a string and look like \"http://HOSTNAMEORADDR:PORT\", not: %r" % (options.nodeurl,))

    if not options.rooturi:
        raise ValueError("must provide --dir-uri")

    local_file = args[0]
    vdrive_fname = None
    if len(args) > 1:
        vdrive_fname = args[1]

    return put(options.nodeurl, options.rooturi, vdrive_fname, local_file)

if __name__ == '__main__':
    main()
