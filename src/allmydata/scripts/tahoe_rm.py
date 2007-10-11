#!/usr/bin/env python

import re, socket

NODEURL_RE=re.compile("http://([^:]*)(:([1-9][0-9]*))?")

def rm(nodeurl, root_uri, vdrive_pathname, verbosity):
    """
    @param verbosity: 0, 1, or 2, meaning quiet, verbose, or very verbose

    @return: a Deferred which eventually fires with the exit code
    """
    mo = NODEURL_RE.match(nodeurl)
    host = mo.group(1)
    port = int(mo.group(3))

    url = "/uri/%s/" % root_uri.replace("/","!")
    if vdrive_pathname:
        url += vdrive_pathname

    so = socket.socket()
    so.connect((host, port,))

    CHUNKSIZE=2**16
    data = "DELETE %s HTTP/1.1\r\nConnection: close\r\nHostname: %s\r\n\r\n" % (url, host,)
    sent = so.send(data)

    respbuf = []
    data = so.recv(CHUNKSIZE)
    while data:
        respbuf.append(data)
        data = so.recv(CHUNKSIZE)

    so.shutdown(socket.SHUT_WR)

    data = so.recv(CHUNKSIZE)
    while data:
        respbuf.append(data)
        data = so.recv(CHUNKSIZE)

    respstr = ''.join(respbuf)

    headerend = respstr.find('\r\n\r\n')
    if headerend == -1:
        headerend = len(respstr)
    header = respstr[:headerend]
    RESP_RE=re.compile("^HTTP/[0-9]\.[0-9] ([0-9]*) *([A-Za-z_ ]*)")  # This regex is soooo ad hoc...  --Zooko 2007-08-16
    mo = RESP_RE.match(header)
    if mo:
        code = int(mo.group(1))
        word = mo.group(2)

        if code == 200:
            print "%s %s" % (code, word,)
            return 0
    
    print respstr[headerend:]
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
