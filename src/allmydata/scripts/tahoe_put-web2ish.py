#!/usr/bin/env python

import re, sys, urllib

from twisted.web2 import stream
from twisted.web2.client.http import HTTPClientProtocol, ClientRequest
from twisted.internet import defer, reactor, protocol

SERVERURL_RE=re.compile("http://([^:]*)(:([1-9][0-9]*))?")

def _put(serverurl, vdrive_fname, local_fname, verbosity):
    """
    @param verbosity: 0, 1, or 2, meaning quiet, verbose, or very verbose

    @return: a Deferred which eventually fires with the exit code
    """
    mo = SERVERURL_RE.match(serverurl)
    if not mo:
        raise ValueError("serverurl is required to look like \"http://HOSTNAMEORADDR:PORT\"")
    host = mo.group(1)
    port = int(mo.group(3))

    d = defer.Deferred()

    url = "/vdrive/global/"
    if vdrive_fname:
        url += urllib.quote(vdrive_fname)

    if local_fname is None or local_fname == "-":
        infileobj = sys.stdin
    else:
        infileobj = open(local_fname, "rb")
    instream = stream.FileStream(infileobj)

    d2 = protocol.ClientCreator(reactor, HTTPClientProtocol).connectTCP(host, port)

    def got_resp(resp):
        # If this isn't a 200 or 201, then write out the response data and
        # exit with resp.code as our exit value.
        if resp.code not in (200, 201,):
            def writeit(data):
                sys.stdout.write(data)

            def exit(dummy):
                d.errback(resp.code)

            return stream.readStream(resp.stream, writeit).addCallback(exit)

        # If we are in quiet mode, then just exit with the resp.code.
        if verbosity == 0:
            d.callback(resp.code)
            return

        # Else, this is a successful request and we are not in quiet mode:
        uribuffer = []
        def gather_uri(data):
            uribuffer.append(data)

        def output_result(thingie):
            uri = ''.join(uribuffer)
            outbuf = []
            if resp.code == 200:
                outbuf.append("200 (OK); ")
            elif resp.code == 201:
                outbuf.append("201 (Created); ")

            if verbosity == 2:
                if resp.code == 200:
                    outbuf.append("modified existing mapping of name %s to point to " % (vdrive_fname,))
                elif resp.code == 201:
                    outbuf.append("created new mapping of name %s to point to " % (vdrive_fname,))

            outbuf.append("URI: %s" % (uri,))

            sys.stdout.write(''.join(outbuf))
            sys.stdout.write("\n")

            d.callback(resp.code)

        stream.readStream(resp.stream, gather_uri).addCallback(output_result)

    def send_req(proto):
        proto.submitRequest(ClientRequest('PUT', url, {}, instream)).addCallback(got_resp)

    d2.addCallback(send_req)

    return d

def put(server, vdrive_fname, local_fname, verbosity):
    """
    This starts the reactor, does the PUT command, waits for the result, stops
    the reactor, and returns the exit code.

    @param verbosity: 0, 1, or 2, meaning quiet, verbose, or very verbose

    @return: the exit code
    """
    d = _put(server, vdrive_fname, local_fname, verbosity)
    exitcode = [ None ]
    def exit(result):
        exitcode[0] = result
        reactor.stop()
        return result

    d.addCallbacks(exit, exit)
    reactor.run()
    return exitcode[0]

