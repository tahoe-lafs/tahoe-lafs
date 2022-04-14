"""Utilities for generating TLS certificates."""

import datetime

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization, hashes

from twisted.python.filepath import FilePath


def cert_to_file(path: FilePath, cert) -> FilePath:
    """
    Write the given certificate to a file on disk. Returns the path.
    """
    path.setContent(cert.public_bytes(serialization.Encoding.PEM))
    return path


def private_key_to_file(path: FilePath, private_key) -> FilePath:
    """
    Write the given key to a file on disk. Returns the path.
    """
    path.setContent(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return path


def generate_private_key():
    """Create a RSA private key."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def generate_certificate(
    private_key,
    expires_days: int = 10,
    valid_in_days: int = 0,
    org_name: str = "Yoyodyne",
):
    """Generate a certificate from a RSA private key."""
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.ORGANIZATION_NAME, org_name)]
    )
    starts = datetime.datetime.utcnow() + datetime.timedelta(days=valid_in_days)
    expires = datetime.datetime.utcnow() + datetime.timedelta(days=expires_days)
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(min(starts, expires))
        .not_valid_after(expires)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
            # Sign our certificate with our private key
        )
        .sign(private_key, hashes.SHA256())
    )
