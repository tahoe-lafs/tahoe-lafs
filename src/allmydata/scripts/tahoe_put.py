#!/usr/bin/env python

import re, socket, sys

NODEURL_RE=re.compile("http://([^:]*)(:([1-9][0-9]*))?")

def put(nodeurl, local_fname, vdrive_fname, verbosity):
    """
    @param verbosity: 0, 1, or 2, meaning quiet, verbose, or very verbose

    @return: a Deferred which eventually fires with the exit code
    """
    mo = NODEURL_RE.match(nodeurl)
    host = mo.group(1)
    port = int(mo.group(3))

    url = "/vdrive/global/"
    if vdrive_fname:
        url += vdrive_fname

    infileobj = open(local_fname, "rb")
    infileobj.seek(0, 2)
    infilelen = infileobj.tell()
    infileobj.seek(0, 0)

    so = socket.socket()
    so.connect((host, port,))

    CHUNKSIZE=2**16
    data = "PUT %s HTTP/1.1\r\nConnection: close\r\nContent-Length: %s\r\nHostname: %s\r\n\r\n" % (url, infilelen, host,)
    while data:
        try:
            sent = so.send(data)
        except Exception, le:
            print "got socket error: %s" % (le,)
            return -1

        if sent == len(data):
            data = infileobj.read(CHUNKSIZE)
        else:
            data = data[sent:]

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

        if code in (200, 201,):
            print "%s %s" % (code, word,)
            return 0
    
    print respstr[headerend:]
    return 1

def main():
    import optparse, re
    parser = optparse.OptionParser()
    parser.add_option("-u", "--node-url", dest="nodeurl")

    (options, args) = parser.parse_args()

    NODEURL_RE=re.compile("http://([^:]*)(:([1-9][0-9]*))?")
    if not isinstance(options.nodeurl, basestring) or not NODEURL_RE.match(options.nodeurl):
        raise ValueError("--node-url is required to be a string and look like \"http://HOSTNAMEORADDR:PORT\", not: %r" % (options.nodeurl,))
    
    local_file = args[0]
    vdrive_fname = None
    if len(args) > 1:
        vdrive_fname = args[1]

    return put(options.nodeurl, vdrive_fname, local_file)

if __name__ == '__main__':
    main()
