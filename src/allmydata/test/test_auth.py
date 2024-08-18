"""
Ported to Python 3.
"""

from typing import Literal

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    text,
    characters,
    lists,
)

from twisted.trial import unittest
from twisted.python import filepath
from twisted.cred import error, credentials
from twisted.conch import error as conch_error
from twisted.conch.ssh import keys

from allmydata.frontends import auth
from allmydata.util.fileutil import abspath_expanduser_unicode


DUMMY_KEY = keys.Key.fromString("""\
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQDEP3DYiukOu+NrUlBZeLL9JoHkK5nSvINYfeOQWYVW9J5NG485
pZFVUQKzvvht34Ihj4ucrrvj7vOp+FFvzxI+zHKBpDxyJwV96dvWDAZMjxTxL7iV
8HcO7hqgtQ/Xk1Kjde5lH3EOEDs3IhFHA+sox9y6i4A5NUr2AJZSHiOEVwIDAQAB
AoGASrrNwefDr7SkeS2zIx7vKa8ML1LbFIBsk7n8ee9c8yvbTAl+lLkTiqV6ne/O
sig2aYk75MI1Eirf5o2ElUsI6u36i6AeKL2u/W7tLBVijmBB8dTiWZ5gMOARWt8w
daF2An2826YdcU+iNZ7Yi0q4xtlxHQn3JcNNWxicphLvt0ECQQDtajJ/bK+Nqd9j
/WGvqYcMzkkorQq/0+MQYhcIwDlpf2Xoi45tP4HeoBubeJmU5+jXpXmdP5epWpBv
k3ZCwV7pAkEA05xBP2HTdwRFTJov5I/w7uKOrn7mj7DCvSjQFCufyPOoCJJMeBSq
tfCQlHFtwlkyNfiSbhtgZ0Pp6ovL+1RBPwJBAOlFRBKxrpgpxcXQK5BWqMwrT/S4
eWxb+6mYR3ugq4h91Zq0rJ+pG6irdhS/XV/SsZRZEXIxDoom4u3OXQ9gQikCQErM
ywuaiuNhMRXY0uEaOHJYx1LLLLjSJKQ0zwiyOvMPnfAZtsojlAxoEtNGHSQ731HQ
ogIlzzfxe7ga3mni6IUCQQCwNK9zwARovcQ8nByqotGQzohpl+1b568+iw8GXP2u
dBSD8940XU3YW+oeq8e+p3yQ2GinHfeJ3BYQyNQLuMAJ
-----END RSA PRIVATE KEY-----
""")

DUMMY_KEY_DSA = keys.Key.fromString("""\
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAABsQAAAAdzc2gtZH
NzAAAAgQDKMh/ELaiP21LYRBuPbUy7dUhv/XZwV7aS1LzxSP+KaJvtDOei8X76XEAfkqX+
aGh9eup+BLkezrV6LlpO9uPzhY8ChlKpkvw5PZKv/2agSrVxZyG7yEzHNtSBQXE6qNMwIk
N/ycXLGCqyAhQSzRhLz9ETNaslRDLo7YyVWkiuAQAAABUA5nTatFKux5EqZS4EarMWFRBU
i1UAAACAFpkkK+JsPixSTPyn0DNMoGKA0Klqy8h61Ds6pws+4+aJQptUBshpwNw1ypo7MO
+goDZy3wwdWtURTPGMgesNdEfxp8L2/kqE4vpMK0myoczCqOiWMeNB/x1AStbSkBI8WmHW
2htgsC01xbaix/FrA3edK8WEyv+oIxlbV1FkrPkAAACANb0EpCc8uoR4/32rO2JLsbcLBw
H5wc2khe7AKkIa9kUknRIRvoCZUtXF5XuXXdRmnpVEm2KcsLdtZjip43asQcqgt0Kz3nuF
kAf7bI98G1waFUimcCSPsal4kCmW2HC11sg/BWOt5qczX/0/3xVxpo6juUeBq9ncnFTvPX
5fOlEAAAHoJkFqHiZBah4AAAAHc3NoLWRzcwAAAIEAyjIfxC2oj9tS2EQbj21Mu3VIb/12
cFe2ktS88Uj/imib7QznovF++lxAH5Kl/mhofXrqfgS5Hs61ei5aTvbj84WPAoZSqZL8OT
2Sr/9moEq1cWchu8hMxzbUgUFxOqjTMCJDf8nFyxgqsgIUEs0YS8/REzWrJUQy6O2MlVpI
rgEAAAAVAOZ02rRSrseRKmUuBGqzFhUQVItVAAAAgBaZJCvibD4sUkz8p9AzTKBigNCpas
vIetQ7OqcLPuPmiUKbVAbIacDcNcqaOzDvoKA2ct8MHVrVEUzxjIHrDXRH8afC9v5KhOL6
TCtJsqHMwqjoljHjQf8dQErW0pASPFph1tobYLAtNcW2osfxawN3nSvFhMr/qCMZW1dRZK
z5AAAAgDW9BKQnPLqEeP99qztiS7G3CwcB+cHNpIXuwCpCGvZFJJ0SEb6AmVLVxeV7l13U
Zp6VRJtinLC3bWY4qeN2rEHKoLdCs957hZAH+2yPfBtcGhVIpnAkj7GpeJAplthwtdbIPw
VjreanM1/9P98VcaaOo7lHgavZ3JxU7z1+XzpRAAAAFQC7360pZLbv7PFt4BPFJ8zAHxAe
QwAAAA5leGFya3VuQGJhcnlvbgECAwQ=
-----END OPENSSH PRIVATE KEY-----
""")

ACCOUNTS = u"""\
# dennis {key} URI:DIR2:aaaaaaaaaaaaaaaaaaaaaaaaaa:1111111111111111111111111111111111111111111111111111
carol {key} URI:DIR2:cccccccccccccccccccccccccc:3333333333333333333333333333333333333333333333333333
""".format(key=str(DUMMY_KEY.public().toString("openssh"), "ascii")).encode("ascii")

# Python str.splitlines considers NEXT LINE, LINE SEPARATOR, and PARAGRAPH
# separator to be line separators, too.  However, file.readlines() does not...
LINE_SEPARATORS = (
    '\x0a', # line feed
    '\x0b', # vertical tab
    '\x0c', # form feed
    '\x0d', # carriage return
)

SURROGATES: Literal["Cs"] = "Cs"


class AccountFileParserTests(unittest.TestCase):
    """
    Tests for ``load_account_file`` and its helper functions.
    """
    @given(lists(
        text(alphabet=characters(
            blacklist_categories=(
                # Surrogates are an encoding trick to help out UTF-16.
                # They're not necessary to represent any non-surrogate code
                # point in unicode.  They're also not legal individually but
                # only in pairs.
                SURROGATES,
            ),
            # Exclude all our line separators too.
            blacklist_characters=("\n", "\r"),
        )),
    ))
    def test_ignore_comments(self, lines):
        """
        ``auth.content_lines`` filters out lines beginning with `#` and empty
        lines.
        """
        expected = set()

        # It's not clear that real files and StringIO behave sufficiently
        # similarly to use the latter instead of the former here.  In
        # particular, they seem to have distinct and incompatible
        # line-splitting rules.
        bufpath = self.mktemp()
        with open(bufpath, "wt", encoding="utf-8") as buf:
            for line in lines:
                stripped = line.strip()
                is_content = stripped and not stripped.startswith("#")
                if is_content:
                    expected.add(stripped)
                buf.write(line + "\n")

        with auth.open_account_file(bufpath) as buf:
            actual = set(auth.content_lines(buf))

        self.assertEqual(expected, actual)

    def test_parse_accounts(self):
        """
        ``auth.parse_accounts`` accepts an iterator of account lines and returns
        an iterator of structured account data.
        """
        alice_key = DUMMY_KEY.public().toString("openssh").decode("utf-8")
        alice_cap = "URI:DIR2:aaaa:1111"

        bob_key = DUMMY_KEY_DSA.public().toString("openssh").decode("utf-8")
        bob_cap = "URI:DIR2:aaaa:2222"
        self.assertEqual(
            list(auth.parse_accounts([
                "alice {} {}".format(alice_key, alice_cap),
                "bob {} {}".format(bob_key, bob_cap),
            ])),
            [
                ("alice", DUMMY_KEY.public(), alice_cap),
                ("bob", DUMMY_KEY_DSA.public(), bob_cap),
            ],
        )

    def test_parse_accounts_rejects_passwords(self):
        """
        The iterator returned by ``auth.parse_accounts`` raises ``ValueError``
        when processing reaches a line that has what looks like a password
        instead of an ssh key.
        """
        with self.assertRaises(ValueError):
            list(auth.parse_accounts(["alice apassword URI:DIR2:aaaa:1111"]))

    def test_create_account_maps(self):
        """
        ``auth.create_account_maps`` accepts an iterator of structured account
        data and returns two mappings: one from account name to rootcap, the
        other from account name to public keys.
        """
        alice_cap = "URI:DIR2:aaaa:1111"
        alice_key = DUMMY_KEY.public()
        bob_cap = "URI:DIR2:aaaa:2222"
        bob_key = DUMMY_KEY_DSA.public()
        accounts = [
            ("alice", alice_key, alice_cap),
            ("bob", bob_key, bob_cap),
        ]
        self.assertEqual(
            auth.create_account_maps(accounts),
            ({
                b"alice": alice_cap.encode("utf-8"),
                b"bob": bob_cap.encode("utf-8"),
            },
             {
                 b"alice": [alice_key],
                 b"bob": [bob_key],
             }),
        )

    def test_load_account_file(self):
        """
        ``auth.load_account_file`` accepts an iterator of serialized account lines
        and returns two mappings: one from account name to rootcap, the other
        from account name to public keys.
        """
        alice_key = DUMMY_KEY.public().toString("openssh").decode("utf-8")
        alice_cap = "URI:DIR2:aaaa:1111"

        bob_key = DUMMY_KEY_DSA.public().toString("openssh").decode("utf-8")
        bob_cap = "URI:DIR2:aaaa:2222"

        accounts = [
            "alice {} {}".format(alice_key, alice_cap),
            "bob {} {}".format(bob_key, bob_cap),
            "# carol {} {}".format(alice_key, alice_cap),
        ]

        self.assertEqual(
            auth.load_account_file(accounts),
            ({
                b"alice": alice_cap.encode("utf-8"),
                b"bob": bob_cap.encode("utf-8"),
            },
             {
                 b"alice": [DUMMY_KEY.public()],
                 b"bob": [DUMMY_KEY_DSA.public()],
             }),
        )


class AccountFileCheckerKeyTests(unittest.TestCase):
    """
    Tests for key handling done by allmydata.frontends.auth.AccountFileChecker.
    """
    def setUp(self):
        self.account_file = filepath.FilePath(self.mktemp())
        self.account_file.setContent(ACCOUNTS)
        abspath = abspath_expanduser_unicode(str(self.account_file.path))
        self.checker = auth.AccountFileChecker(None, abspath)

    def test_unknown_user(self):
        """
        AccountFileChecker.requestAvatarId returns a Deferred that fires with
        UnauthorizedLogin if called with an SSHPrivateKey object with a
        username not present in the account file.
        """
        key_credentials = credentials.SSHPrivateKey(
            b"dennis", b"md5", None, None, None)
        avatarId = self.checker.requestAvatarId(key_credentials)
        return self.assertFailure(avatarId, error.UnauthorizedLogin)

    def test_unrecognized_key(self):
        """
        AccountFileChecker.requestAvatarId returns a Deferred that fires with
        UnauthorizedLogin if called with an SSHPrivateKey object with a public
        key other than the one indicated in the account file for the indicated
        user.
        """
        wrong_key_blob = b"""\
ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAAYQDJGMWlPXh2M3pYzTiamjcBIMqctt4VvLVW2QZgEFc86XhGjPXq5QAiRTKv9yVZJR9HW70CfBI7GHun8+v4Wb6aicWBoxgI3OB5NN+OUywdme2HSaif5yenFdQr0ME71Xs=
"""
        key_credentials = credentials.SSHPrivateKey(
            b"carol", b"md5", wrong_key_blob, None, None)
        avatarId = self.checker.requestAvatarId(key_credentials)
        return self.assertFailure(avatarId, error.UnauthorizedLogin)

    def test_missing_signature(self):
        """
        AccountFileChecker.requestAvatarId returns a Deferred that fires with
        ValidPublicKey if called with an SSHPrivateKey object with an
        authorized key for the indicated user but with no signature.
        """
        right_key_blob = DUMMY_KEY.public().toString("openssh")
        key_credentials = credentials.SSHPrivateKey(
            b"carol", b"md5", right_key_blob, None, None)
        avatarId = self.checker.requestAvatarId(key_credentials)
        return self.assertFailure(avatarId, conch_error.ValidPublicKey)

    def test_wrong_signature(self):
        """
        AccountFileChecker.requestAvatarId returns a Deferred that fires with
        UnauthorizedLogin if called with an SSHPrivateKey object with a public
        key matching that on the user's line in the account file but with the
        wrong signature.
        """
        right_key_blob = DUMMY_KEY.public().toString("openssh")
        key_credentials = credentials.SSHPrivateKey(
            b"carol", b"md5", right_key_blob, b"signed data", b"wrong sig")
        avatarId = self.checker.requestAvatarId(key_credentials)
        return self.assertFailure(avatarId, error.UnauthorizedLogin)

    def test_authenticated(self):
        """
        If called with an SSHPrivateKey object with a username and public key
        found in the account file and a signature that proves possession of the
        corresponding private key, AccountFileChecker.requestAvatarId returns a
        Deferred that fires with an FTPAvatarID giving the username and root
        capability for that user.
        """
        username = b"carol"
        signed_data = b"signed data"
        signature = DUMMY_KEY.sign(signed_data)
        right_key_blob = DUMMY_KEY.public().toString("openssh")
        key_credentials = credentials.SSHPrivateKey(
            username, b"md5", right_key_blob, signed_data, signature)
        avatarId = self.checker.requestAvatarId(key_credentials)
        def authenticated(avatarId):
            self.assertEqual(
                (username,
                 b"URI:DIR2:cccccccccccccccccccccccccc:3333333333333333333333333333333333333333333333333333"),
                (avatarId.username, avatarId.rootcap))
        avatarId.addCallback(authenticated)
        return avatarId
