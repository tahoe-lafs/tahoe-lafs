# -*- test-case-name: foolscap.test.test_crypto -*-

available = False # hack to deal with half-broken imports in python <2.4

from OpenSSL import SSL

# we try to use ssl support classes from Twisted, if it is new enough. If
# not, we pull them from a local copy of sslverify. The funny '_ssl' import
# stuff is used to appease pyflakes, which otherwise complains that we're
# redefining an imported name.
from twisted.internet import ssl
if hasattr(ssl, "DistinguishedName"):
    # Twisted-2.5 will contain these names
    _ssl = ssl
    CertificateOptions = ssl.CertificateOptions
else:
    # but it hasn't been released yet (as of 16-Sep-2006). Without them, we
    # cannot use any encrypted Tubs. We fall back to using a private copy of
    # sslverify.py, copied from the Divmod tree.
    import sslverify
    _ssl = sslverify
    from sslverify import OpenSSLCertificateOptions as CertificateOptions
DistinguishedName = _ssl.DistinguishedName
KeyPair = _ssl.KeyPair
Certificate = _ssl.Certificate
PrivateCertificate = _ssl.PrivateCertificate

from twisted.internet import error

if hasattr(error, "CertificateError"):
    # Twisted-2.4 contains this, and it is used by twisted.internet.ssl
    CertificateError = error.CertificateError
else:
    class CertificateError(Exception):
        """
        We did not find a certificate where we expected to find one.
        """


from foolscap import base32

peerFromTransport = Certificate.peerFromTransport

class MyOptions(CertificateOptions):
    def _makeContext(self):
        ctx = CertificateOptions._makeContext(self)
        def alwaysValidate(conn, cert, errno, depth, preverify_ok):
            # This function is called to validate the certificate received by
            # the other end. OpenSSL calls it multiple times, each time it
            # see something funny, to ask if it should proceed.

            # We do not care about certificate authorities or revocation
            # lists, we just want to know that the certificate has a valid
            # signature and follow the chain back to one which is
            # self-signed. The TubID will be the digest of one of these
            # certificates. We need to protect against forged signatures, but
            # not the usual SSL concerns about invalid CAs or revoked
            # certificates.

            # these constants are from openssl-0.9.7g/crypto/x509/x509_vfy.h
            # and do not appear to be exposed by pyopenssl. Ick. TODO. We
            # could just always return '1' here (ignoring all errors), but I
            # think that would ignore forged signatures too, which would
            # obviously be a security hole.
            things_are_ok = (0,  # X509_V_OK
                             9, # X509_V_ERR_CERT_NOT_YET_VALID
                             10, # X509_V_ERR_CERT_HAS_EXPIRED
                             18, # X509_V_ERR_DEPTH_ZERO_SELF_SIGNED_CERT
                             19, # X509_V_ERR_SELF_SIGNED_CERT_IN_CHAIN
                             )
            if errno in things_are_ok:
                return 1
            # TODO: log the details of the error, because otherwise they get
            # lost in the PyOpenSSL exception that will eventually be raised
            # (possibly OpenSSL.SSL.Error: certificate verify failed)

            # I think that X509_V_ERR_CERT_SIGNATURE_FAILURE is the most
            # obvious sign of hostile attack.
            return 0

        # VERIFY_PEER means we ask the the other end for their certificate.
        # not adding VERIFY_FAIL_IF_NO_PEER_CERT means it's ok if they don't
        # give us one (i.e. if an anonymous client connects to an
        # authenticated server). I don't know what VERIFY_CLIENT_ONCE does.
        ctx.set_verify(SSL.VERIFY_PEER |
                       #SSL.VERIFY_FAIL_IF_NO_PEER_CERT |
                       SSL.VERIFY_CLIENT_ONCE,
                       alwaysValidate)
        return ctx

def digest32(colondigest):
    digest = "".join([chr(int(c,16)) for c in colondigest.split(":")])
    digest = base32.encode(digest)
    return digest

available = True
