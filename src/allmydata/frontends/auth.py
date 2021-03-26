import os

import future.builtins

from zope.interface import implementer
from twisted.web.client import getPage
from twisted.internet import defer
from twisted.cred import error, checkers, credentials
from twisted.conch.ssh import keys
from twisted.conch.checkers import SSHPublicKeyChecker, InMemorySSHKeyDB

from allmydata.util.dictutil import BytesKeyDict
from allmydata.util import base32
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
        d.addCallback(self._cbPasswordMatch, str(creds.username))
        return d


@implementer(checkers.ICredentialsChecker)
class AccountURLChecker(object):
    credentialInterfaces = (credentials.IUsernamePassword,)

    def __init__(self, client, auth_url):
        self.client = client
        self.auth_url = auth_url

    def _cbPasswordMatch(self, rootcap, username):
        return FTPAvatarID(username, rootcap)

    @staticmethod
    def _build_multipart(**fields):
        """
        Build headers and body for a multipart form request
        containing the supplied fields.
        """
        sepbase = base32.b2a(os.urandom(4)).decode('ascii')
        sep = "--" + sepbase
        form = []
        form.append(sep)
        for name, value in fields.items():
            form.append('Content-Disposition: form-data; name="%s"' % name)
            form.append('')
            assert isinstance(value, (future.builtins.str, bytes))
            form.append(value)
            form.append(sep)
        form[-1] += "--"
        body = "\r\n".join(form) + "\r\n"
        content_type = "multipart/form-data; boundary=%s" % sepbase
        headers = {"content-type": content_type}
        return headers, body

    def post_form(self, username, password):
        mp = self._build_multipart(
            action="authenticate",
            email=username,
            passwd=password,
        )
        # getPage needs everything in bytes.
        headers, body = map(_encode_all, mp)
        return getPage(self.auth_url, method="POST",
                       postdata=body, headers=headers,
                       followRedirect=True, timeout=30)

    def _parse_response(self, res):
        rootcap = res.strip()
        if rootcap == "0":
            raise error.UnauthorizedLogin
        return rootcap

    def requestAvatarId(self, credentials):
        # construct a POST to the login form. While this could theoretically
        # be done with something like the stdlib 'email' package, I can't
        # figure out how, so we just slam together a form manually.
        d = self.post_form(credentials.username, credentials.password)
        d.addCallback(self._parse_response)
        d.addCallback(self._cbPasswordMatch, str(credentials.username))
        return d


def _encode_all(val):
    """
    Encode text or a dict to bytes using utf-8.
    TODO: Consider using singledispatch.
    """
    if isinstance(val, dict):
        return {
            _encode_all(key): _encode_all(value)
            for key, value in val.items()
        }
    return val.encode('utf-8')
