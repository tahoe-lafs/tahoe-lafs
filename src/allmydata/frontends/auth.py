"""
Authentication for frontends.
"""
from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from zope.interface import implementer
from twisted.internet import defer
from twisted.cred import error, checkers, credentials
from twisted.conch.ssh import keys
from twisted.conch.checkers import SSHPublicKeyChecker, InMemorySSHKeyDB

from allmydata.util.dictutil import BytesKeyDict
from allmydata.util.fileutil import abspath_expanduser_unicode


class NeedRootcapLookupScheme(Exception):
    """Accountname+Password-based access schemes require some kind of
    mechanism to translate name+passwd pairs into a rootcap, either a file of
    name/passwd/rootcap tuples, or a server to do the translation."""

class FTPAvatarID(object):
    def __init__(self, username, rootcap):
        self.username = username
        self.rootcap = rootcap

@implementer(checkers.ICredentialsChecker)
class AccountFileChecker(object):
    credentialInterfaces = (credentials.IUsernamePassword,
                            credentials.IUsernameHashedPassword,
                            credentials.ISSHPrivateKey)
    def __init__(self, client, accountfile):
        self.client = client
        self.passwords = BytesKeyDict()
        pubkeys = BytesKeyDict()
        self.rootcaps = BytesKeyDict()
        with open(abspath_expanduser_unicode(accountfile), "rb") as f:
            for line in f:
                line = line.strip()
                if line.startswith(b"#") or not line:
                    continue
                name, passwd, rest = line.split(None, 2)
                if passwd.startswith(b"ssh-"):
                    bits = rest.split()
                    keystring = b" ".join([passwd] + bits[:-1])
                    key = keys.Key.fromString(keystring)
                    rootcap = bits[-1]
                    pubkeys[name] = [key]
                else:
                    self.passwords[name] = passwd
                    rootcap = rest
                self.rootcaps[name] = rootcap
        self._pubkeychecker = SSHPublicKeyChecker(InMemorySSHKeyDB(pubkeys))

    def _avatarId(self, username):
        return FTPAvatarID(username, self.rootcaps[username])

    def _cbPasswordMatch(self, matched, username):
        if matched:
            return self._avatarId(username)
        raise error.UnauthorizedLogin

    def requestAvatarId(self, creds):
        if credentials.ISSHPrivateKey.providedBy(creds):
            d = defer.maybeDeferred(self._pubkeychecker.requestAvatarId, creds)
            d.addCallback(self._avatarId)
            return d
        elif credentials.IUsernameHashedPassword.providedBy(creds):
            return self._checkPassword(creds)
        elif credentials.IUsernamePassword.providedBy(creds):
            return self._checkPassword(creds)
        else:
            raise NotImplementedError()

    def _checkPassword(self, creds):
        """
        Determine whether the password in the given credentials matches the
        password in the account file.

        Returns a Deferred that fires with the username if the password matches
        or with an UnauthorizedLogin failure otherwise.
        """
        try:
            correct = self.passwords[creds.username]
        except KeyError:
            return defer.fail(error.UnauthorizedLogin())

        d = defer.maybeDeferred(creds.checkPassword, correct)
        d.addCallback(self._cbPasswordMatch, creds.username)
        return d
