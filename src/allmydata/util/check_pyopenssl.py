#!/usr/bin/python

import re


class UnsafeOpenSSLError(EnvironmentError):
    pass


def check_openssl_version(SSL):
    openssl_version = SSL.SSLeay_version(SSL.SSLEAY_VERSION)
    split_version = openssl_version.split(' ')

    if len(split_version) < 2 or split_version[0] != 'OpenSSL':
        raise UnsafeOpenSSLError("could not understand OpenSSL version string %s" % (openssl_version,))

    try:
        components = split_version[1].split('.')
        numeric_components = map(int, components[:2])
        if len(components) > 2:
            m = re.match(r'[0-9]*', components[2])
            numeric_components += [int(m.group(0))]

        #print numeric_components
        if ((numeric_components == [0, 9, 8] and components[2] >= '8y') or
            (numeric_components == [1, 0, 0] and components[2] >= '0l') or
            (numeric_components == [1, 0, 1] and components[2] >= '1g') or
            (numeric_components == [1, 0, 2] and not components[2].startswith('2-beta')) or
            (numeric_components >= [1, 0, 3])):
            return

        if numeric_components == [1, 0, 1]:
            # Also allow versions 1.0.1 through 1.0.1f if a Heartbleed vulnerability test passes.
            # We assume that a library patched for Heartbleed is also patched for previous
            # security bugs that affected 1.0.1 through 1.0.1c.
            #
            # We do this check only if the version check above is inconclusive, to minimize the
            # chance for the test to break or give the wrong result somehow.
            check_resistant_to_heartbleed(SSL)

    except Exception, e:
        #import traceback
        #traceback.print_exc()
        pass

    raise UnsafeOpenSSLError("refusing to use %s which may be vulnerable to security bugs.\n"
                             "Please upgrade to OpenSSL 1.0.1g or later." % (openssl_version,))


# As simple as possible, but no simpler.
_CLIENT_HELLO = (
  '\x16'                 # Handshake
  '\x03\x01'             # TLS version 1.0
  '\x00\x34'             # length of ClientHello
  '\x01'                 #   Handshake type (ClientHello)
  '\x00\x00\x30'         #   length
  '\x03\x01'             #     TLS version 1.0
  '\x53\x43\x5b\x90'     #     timestamp
  '\x9d\x9b\x72\x0b\xbc\x0c\xbc\x2b\x92\xa8\x48\x97\xcf\xbd'
  '\x39\x04\xcc\x16\x0a\x85\x03\x90\x9f\x77\x04\x33\xd4\xde' # client random
  '\x00'                 #     length of session ID (not resuming session)
  '\x00\x02'             #     length of ciphersuites
  '\x00\x0a'             #       TLS_RSA_WITH_3DES_EDE_CBC_SHA
  '\x01'                 #     length of compression methods
  '\x00'                 #       null compression
  '\x00\x05'             #     length of extensions
  '\x00\x0f\x00\x01\x01' #       heartbeat extension
)

_HEARTBEAT = (
  '\x18'                 # Heartbeat
  '\x03\x01'             # TLS version 1.0
  '\x00\x03'             # length
  '\x01'                 #   heartbeat request
  '\x10\x00'             #   payload length (4096 bytes)
)
_HEARTBEAT2 = (
  '\x18'                 # Heartbeat
  '\x03\x01'             # TLS version 1.0
  '\x00\x23'             # length
  '\x01'                 #   heartbeat request
  '\x00\x01'             #   payload length (0 bytes)
) + '\x00'*33

def check_resistant_to_heartbleed(SSL):
    def verify_callback(connection, x509, errnum, errdepth, ok):
        return ok

    if not hasattr(SSL, 'TLSv1_METHOD'):
        # pyOpenSSL is too old. FIXME report this better
        return True

    ctx = SSL.Context(SSL.TLSv1_METHOD)
    ctx.set_options(SSL.OP_NO_SSLv2 | SSL.OP_NO_SSLv3)
    ctx.use_certificate_file('test.crt')
    ctx.use_privatekey_file('test.key')
    ctx.set_cipher_list('DES-CBC3-SHA') # TLS_RSA_WITH_3DES_EDE_CBC_SHA
    ctx.set_verify(SSL.VERIFY_NONE, verify_callback)

    server = SSL.Connection(ctx, None)
    server.set_accept_state()
    server.bio_write(_CLIENT_HELLO + _HEARTBEAT + _HEARTBEAT2)

    server_response = bytearray()
    try:
        server.do_handshake()
    except SSL.WantReadError:
        pass # this is expected

    while True:
        try:
            server_response += server.bio_read(32768)
        except SSL.WantReadError:
            break
    print repr(server_response)
    print len(server_response)

    # Fortunately we don't need to parse anything complicated, just the outer layer.
    i = 0
    while i+5 <= len(server_response):
        record_type = server_response[i]
        # we don't care about the record version
        record_length = (server_response[i+3]<<8) + server_response[i+4]
        print record_type, record_length
        if record_length == 0:
            # avoid infinite loop
            return True
        if record_type == 0x18 and record_length > 3:
            # longer than expected heartbeat response => vulnerable
            return True
        i += 5 + record_length

    if i < len(server_response):
        print "hmm"

    return False


if __name__ == '__main__':
    from OpenSSL import SSL
    #check_openssl_version(SSL)
    check_resistant_to_heartbleed(SSL)
    print "Not vulnerable."

