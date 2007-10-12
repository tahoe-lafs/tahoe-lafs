#!/usr/bin/env python

import urllib
from allmydata.scripts.common_http import do_http

def rm(nodeurl, root_uri, vdrive_pathname, verbosity, stdout, stderr):
    """
    @param verbosity: 0, 1, or 2, meaning quiet, verbose, or very verbose

    @return: a Deferred which eventually fires with the exit code
    """
    if nodeurl[-1] != "/":
        nodeurl += "/"
    url = nodeurl + "uri/%s/" % urllib.quote(root_uri.replace("/","!"))
    if vdrive_pathname:
        url += vdrive_pathname

    resp = do_http("DELETE", url)

    if resp.status in (200,):
        print >>stdout, "%s %s" % (resp.status, resp.reason)
        return 0

    print >>stderr, "error, got %s %s" % (resp.status, resp.reason)
    print >>stderr, resp.read()
    return 1

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
    
    vdrive_pathname = args[0]

    return rm(options.nodeurl, options.rooturi, vdrive_pathname, 0)

if __name__ == '__main__':
    main()
