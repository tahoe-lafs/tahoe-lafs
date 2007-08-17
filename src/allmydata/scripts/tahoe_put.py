#!/usr/bin/env python

import re, socket, sys

SERVERURL_RE=re.compile("http://([^:]*)(:([1-9][0-9]*))?")

def put(nodeurl, vdrive, vdrive_fname, local_fname, verbosity):
    """
    @param verbosity: 0, 1, or 2, meaning quiet, verbose, or very verbose

    @return: a Deferred which eventually fires with the exit code
    """
    if not isinstance(nodeurl, basestring):
        raise ValueError("nodeurl is required to be a string and look like \"http://HOSTNAMEORADDR:PORT\", not: %r" % (nodeurl,))

    mo = SERVERURL_RE.match(nodeurl)
    if not mo:
        raise ValueError("nodeurl is required to look like \"http://HOSTNAMEORADDR:PORT\", not: %r" % (nodeurl,))
    host = mo.group(1)
    port = int(mo.group(3))

    url = "/vdrive/" + vdrive + "/"
    if vdrive_fname:
        url += vdrive_fname

    if local_fname is None or local_fname == "-":
        infileobj = sys.stdin
    else:
        infileobj = open(local_fname, "rb")

    so = socket.socket()
    so.connect((host, port,))

    CHUNKSIZE=2**16
    data = "PUT %s HTTP/1.1\r\nConnection: close\r\nHostname: %s\r\n\r\n" % (url, host,)
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
        # print "debuggery 1 okay now we've got some more data: %r" % (data,)
        respbuf.append(data)
        data = so.recv(CHUNKSIZE)

    so.shutdown(socket.SHUT_WR)

    data = so.recv(CHUNKSIZE)
    while data:
        # print "debuggery 2 okay now we've got some more data: %r" % (data,)
        respbuf.append(data)
        data = so.recv(CHUNKSIZE)

    respstr = ''.join(respbuf)

    RESP_RE=re.compile("^HTTP/[0-9]\.[0-9] ([0-9]*) *([A-Za-z_]*)")  # This regex is soooo ad hoc...  --Zooko 2007-08-16
    mo = RESP_RE.match(respstr)
    if mo:
        code = int(mo.group(1))
        word = mo.group(2)

        if code in (200, 201,):
            print "%s %s" % (code, word,)
            return 0
    
    print respstr
    return 1

def main():
    import optparse
    parser = optparse.OptionParser()
    parser.add_option("-d", "--vdrive", dest="vdrive", default="global")
    parser.add_option("-s", "--server", dest="server", default="http://tahoebs1.allmydata.com:8011")

    (options, args) = parser.parse_args()

    local_file = args[0]
    vdrive_file = None
    if len(args) > 1:
        vdrive_file = args[1]

    return put(options.server, options.vdrive, vdrive_file, local_file)

if __name__ == '__main__':
    main()
