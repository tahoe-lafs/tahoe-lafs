import os
from zope.interface import implements
from twisted.web.client import getPage
from twisted.internet import defer
from twisted.cred import error, checkers, credentials
from allmydata.util import base32

class NeedRootcapLookupScheme(Exception):
    """Accountname+Password-based access schemes require some kind of
    mechanism to translate name+passwd pairs into a rootcap, either a file of
    name/passwd/rootcap tuples, or a server to do the translation."""

class FTPAvatarID:
    def __init__(self, username, rootcap):
        self.username = username
        self.rootcap = rootcap

class AccountFileChecker:
    implements(checkers.ICredentialsChecker)
    credentialInterfaces = (credentials.IUsernamePassword,
                            credentials.IUsernameHashedPassword)
    def __init__(self, client, accountfile):
        self.client = client
        self.passwords = {}
        self.pubkeys = {}
        self.rootcaps = {}
        for line in open(os.path.expanduser(accountfile), "r"):
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            name, passwd, rest = line.split(None, 2)
            if passwd in ("ssh-dss", "ssh-rsa"):
                bits = rest.split()
                keystring = " ".join(bits[-1])
                rootcap = bits[-1]
                self.pubkeys[name] = keystring
            else:
                self.passwords[name] = passwd
                rootcap = rest
            self.rootcaps[name] = rootcap

    def _cbPasswordMatch(self, matched, username):
        if matched:
            return FTPAvatarID(username, self.rootcaps[username])
        raise error.UnauthorizedLogin

    def requestAvatarId(self, credentials):
        if credentials.username in self.passwords:
            d = defer.maybeDeferred(credentials.checkPassword,
                                    self.passwords[credentials.username])
            d.addCallback(self._cbPasswordMatch, str(credentials.username))
            return d
        return defer.fail(error.UnauthorizedLogin())

class AccountURLChecker:
    implements(checkers.ICredentialsChecker)
    credentialInterfaces = (credentials.IUsernamePassword,)

    def __init__(self, client, auth_url):
        self.client = client
        self.auth_url = auth_url

    def _cbPasswordMatch(self, rootcap, username):
        return FTPAvatarID(username, rootcap)

    def post_form(self, username, password):
        sepbase = base32.b2a(os.urandom(4))
        sep = "--" + sepbase
        form = []
        form.append(sep)
        fields = {"action": "authenticate",
                  "email": username,
                  "passwd": password,
                  }
        for name, value in fields.iteritems():
            form.append('Content-Disposition: form-data; name="%s"' % name)
            form.append('')
            assert isinstance(value, str)
            form.append(value)
            form.append(sep)
        form[-1] += "--"
        body = "\r\n".join(form) + "\r\n"
        headers = {"content-type": "multipart/form-data; boundary=%s" % sepbase,
                   }
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

