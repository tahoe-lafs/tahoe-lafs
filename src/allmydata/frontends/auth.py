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
from twisted.cred import checkers, credentials
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
    credentialInterfaces = (credentials.ISSHPrivateKey,)

    def __init__(self, client, accountfile):
        self.client = client
        path = abspath_expanduser_unicode(accountfile)
        with open_account_file(path) as f:
            self.rootcaps, pubkeys = load_account_file(f)
        self._pubkeychecker = SSHPublicKeyChecker(InMemorySSHKeyDB(pubkeys))

    def _avatarId(self, username):
        return FTPAvatarID(username, self.rootcaps[username])

    def requestAvatarId(self, creds):
        if credentials.ISSHPrivateKey.providedBy(creds):
            d = defer.maybeDeferred(self._pubkeychecker.requestAvatarId, creds)
            d.addCallback(self._avatarId)
            return d
        raise NotImplementedError()

def open_account_file(path):
    """
    Open and return the accounts file at the given path.
    """
    return open(path, "rt", encoding="utf-8")

def load_account_file(lines):
    """
    Load credentials from an account file.

    :param lines: An iterable of account lines to load.

    :return: See ``create_account_maps``.
    """
    return create_account_maps(
        parse_accounts(
            content_lines(
                lines,
            ),
        ),
    )

def content_lines(lines):
    """
    Drop empty and commented-out lines (``#``-prefixed) from an iterator of
    lines.

    :param lines: An iterator of lines to process.

    :return: An iterator of lines including only those from ``lines`` that
        include content intended to be loaded.
    """
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            yield line

def parse_accounts(lines):
    """
    Parse account lines into their components (name, key, rootcap).
    """
    for line in lines:
        name, passwd, rest = line.split(None, 2)
        if not passwd.startswith("ssh-"):
            raise ValueError(
                "Password-based authentication is not supported; "
                "configure key-based authentication instead."
            )

        bits = rest.split()
        keystring = " ".join([passwd] + bits[:-1])
        key = keys.Key.fromString(keystring)
        rootcap = bits[-1]
        yield (name, key, rootcap)

def create_account_maps(accounts):
    """
    Build mappings from account names to keys and rootcaps.

    :param accounts: An iterator if (name, key, rootcap) tuples.

    :return: A tuple of two dicts.  The first maps account names to rootcaps.
        The second maps account names to public keys.
    """
    rootcaps = BytesKeyDict()
    pubkeys = BytesKeyDict()
    for (name, key, rootcap) in accounts:
        name_bytes = name.encode("utf-8")
        rootcaps[name_bytes] = rootcap.encode("utf-8")
        pubkeys[name_bytes] = [key]
    return rootcaps, pubkeys
