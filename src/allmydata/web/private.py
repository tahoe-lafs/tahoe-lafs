"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import attr

from zope.interface import (
    implementer,
)

from twisted.python.failure import (
    Failure,
)
from twisted.internet.defer import (
    succeed,
    fail,
)
from twisted.cred.credentials import (
    ICredentials,
)
from twisted.cred.portal import (
    IRealm,
    Portal,
)
from twisted.cred.checkers import (
    ANONYMOUS,
)
from twisted.cred.error import (
    UnauthorizedLogin,
)
from twisted.web.iweb import (
    ICredentialFactory,
)
from twisted.web.resource import (
    IResource,
    Resource,
)
from twisted.web.guard import (
    HTTPAuthSessionWrapper,
)

from ..util.hashutil import (
    timing_safe_compare,
)
from ..util.assertutil import (
    precondition,
)

from .logs import (
    create_log_resources,
)

SCHEME = b"tahoe-lafs"

class IToken(ICredentials):
    def check(auth_token):
        pass


# Workaround for Shoobx/mypy-zope#26, where without suitable
# stubs for twisted classes (ICredentials), IToken does not
# appear to be an Interface. The proper fix appears to be to
# create stubs for twisted
# (https://twistedmatrix.com/trac/ticket/9717). For now,
# bypassing the inline decorator syntax works around the issue.
_itoken_impl = implementer(IToken)


@_itoken_impl
@attr.s
class Token(object):
    proposed_token = attr.ib(type=bytes)

    def equals(self, valid_token):
        return timing_safe_compare(
            valid_token,
            self.proposed_token,
        )


@attr.s
class TokenChecker(object):
    get_auth_token = attr.ib()

    credentialInterfaces = [IToken]

    def requestAvatarId(self, credentials):
        required_token = self.get_auth_token()
        precondition(isinstance(required_token, bytes))
        if credentials.equals(required_token):
            return succeed(ANONYMOUS)
        return fail(Failure(UnauthorizedLogin()))


@implementer(ICredentialFactory)
@attr.s
class TokenCredentialFactory(object):
    scheme = SCHEME
    authentication_realm = b"tahoe-lafs"

    def getChallenge(self, request):
        return {b"realm": self.authentication_realm}

    def decode(self, response, request):
        return Token(response)


@implementer(IRealm)
@attr.s
class PrivateRealm(object):
    _root = attr.ib()

    def _logout(self):
        pass

    def requestAvatar(self, avatarId, mind, *interfaces):
        if IResource in interfaces:
            return (IResource, self._root, self._logout)
        raise NotImplementedError(
            "PrivateRealm supports IResource not {}".format(interfaces),
        )


def _create_vulnerable_tree():
    private = Resource()
    private.putChild(b"logs", create_log_resources())
    return private


def _create_private_tree(get_auth_token, vulnerable):
    realm = PrivateRealm(vulnerable)
    portal = Portal(realm, [TokenChecker(get_auth_token)])
    return HTTPAuthSessionWrapper(portal, [TokenCredentialFactory()])


def create_private_tree(get_auth_token):
    """
    Create a new resource tree that only allows requests if they include a
    correct `Authorization: tahoe-lafs <api_auth_token>` header (where
    `api_auth_token` matches the private configuration value).
    """
    return _create_private_tree(
        get_auth_token,
        _create_vulnerable_tree(),
    )
