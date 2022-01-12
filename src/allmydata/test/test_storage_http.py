"""
Tests for HTTP storage client + server.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2

if PY2:
    # fmt: off
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
    # fmt: on

from base64 import b64encode

from hypothesis import assume, given, strategies as st
from .common import SyncTestCase
from ..storage.http_server import (
    _extract_secrets,
    Secrets,
    ClientSecretsException,
)


def _post_process(params):
    secret_types, secrets = params
    secrets = {t: s for (t, s) in zip(secret_types, secrets)}
    headers = [
        "{} {}".format(
            secret_type.value, str(b64encode(secrets[secret_type]), "ascii").strip()
        )
        for secret_type in secret_types
    ]
    return secrets, headers


# Creates a tuple of ({Secret enum value: secret_bytes}, [http headers with secrets]).
SECRETS_STRATEGY = (
    st.sets(st.sampled_from(Secrets))
    .flatmap(
        lambda secret_types: st.tuples(
            st.just(secret_types),
            st.lists(
                st.binary(min_size=32, max_size=32),
                min_size=len(secret_types),
                max_size=len(secret_types),
            ),
        )
    )
    .map(_post_process)
)


class ExtractSecretsTests(SyncTestCase):
    """
    Tests for ``_extract_secrets``.
    """

    def setUp(self):
        if PY2:
            self.skipTest("Not going to bother supporting Python 2")
        super(ExtractSecretsTests, self).setUp()

    @given(secrets_to_send=SECRETS_STRATEGY)
    def test_extract_secrets(self, secrets_to_send):
        """
        ``_extract_secrets()`` returns a dictionary with the extracted secrets
        if the input secrets match the required secrets.
        """
        secrets, headers = secrets_to_send

        # No secrets needed, none given:
        self.assertEqual(_extract_secrets(headers, secrets.keys()), secrets)

    @given(
        secrets_to_send=SECRETS_STRATEGY,
        secrets_to_require=st.sets(st.sampled_from(Secrets)),
    )
    def test_wrong_number_of_secrets(self, secrets_to_send, secrets_to_require):
        """
        If the wrong number of secrets are passed to ``_extract_secrets``, a
        ``ClientSecretsException`` is raised.
        """
        secrets_to_send, headers = secrets_to_send
        assume(secrets_to_send.keys() != secrets_to_require)

        with self.assertRaises(ClientSecretsException):
            _extract_secrets(headers, secrets_to_require)

    def test_bad_secret_missing_value(self):
        """
        Missing value in ``_extract_secrets`` result in
        ``ClientSecretsException``.
        """
        with self.assertRaises(ClientSecretsException):
            _extract_secrets(["lease-renew-secret"], {Secrets.LEASE_RENEW})

    def test_bad_secret_unknown_prefix(self):
        """
        Missing value in ``_extract_secrets`` result in
        ``ClientSecretsException``.
        """
        with self.assertRaises(ClientSecretsException):
            _extract_secrets(["FOO eA=="], {})

    def test_bad_secret_not_base64(self):
        """
        A non-base64 value in ``_extract_secrets`` result in
        ``ClientSecretsException``.
        """
        with self.assertRaises(ClientSecretsException):
            _extract_secrets(["lease-renew-secret x"], {Secrets.LEASE_RENEW})

    def test_bad_secret_wrong_length_lease_renew(self):
        """
        Lease renewal secrets must be 32-bytes long.
        """
        with self.assertRaises(ClientSecretsException):
            _extract_secrets(["lease-renew-secret eA=="], {Secrets.LEASE_RENEW})

    def test_bad_secret_wrong_length_lease_cancel(self):
        """
        Lease cancel secrets must be 32-bytes long.
        """
        with self.assertRaises(ClientSecretsException):
            _extract_secrets(["lease-cancel-secret eA=="], {Secrets.LEASE_RENEW})
