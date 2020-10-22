# Python 2 compatibility
from future.utils import PY2
if PY2:
    from future.builtins import str  # noqa: F401

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

DUMMY_ACCOUNTS = u"""\
alice password URI:DIR2:aaaaaaaaaaaaaaaaaaaaaaaaaa:1111111111111111111111111111111111111111111111111111
bob sekrit URI:DIR2:bbbbbbbbbbbbbbbbbbbbbbbbbb:2222222222222222222222222222222222222222222222222222
carol {key} URI:DIR2:cccccccccccccccccccccccccc:3333333333333333333333333333333333333333333333333333
""".format(key=DUMMY_KEY.public().toString("openssh")).encode("ascii")

class AccountFileCheckerKeyTests(unittest.TestCase):
    """
    Tests for key handling done by allmydata.frontends.auth.AccountFileChecker.
    """
    def setUp(self):
        self.account_file = filepath.FilePath(self.mktemp())
        self.account_file.setContent(DUMMY_ACCOUNTS)
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

    def test_password_auth_user(self):
        """
        AccountFileChecker.requestAvatarId returns a Deferred that fires with
        UnauthorizedLogin if called with an SSHPrivateKey object for a username
        only associated with a password in the account file.
        """
        key_credentials = credentials.SSHPrivateKey(
            b"alice", b"md5", None, None, None)
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
