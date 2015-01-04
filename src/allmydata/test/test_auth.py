from twisted.trial import unittest
from twisted.python import filepath
from twisted.cred import error, credentials
from twisted.conch.ssh import keys

from allmydata.frontends import auth

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
carol %(key)s URI:DIR2:cccccccccccccccccccccccccc:3333333333333333333333333333333333333333333333333333
""".format(DUMMY_KEY.public().toString("openssh")).encode("ascii")

class AccountFileCheckerKeyTests(unittest.TestCase):
    """
    Tests for key handling done by allmydata.frontends.auth.AccountFileChecker.
    """
    def setUp(self):
        self.account_file = filepath.FilePath(self.mktemp())
        self.account_file.setContent(DUMMY_ACCOUNTS)
        self.checker = auth.AccountFileChecker(None, self.account_file.path)

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
