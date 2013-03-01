"""
This requires the oauth2client library:

http://code.google.com/p/google-api-python-client/downloads/list
"""

import httplib2

from twisted.internet.defer import DeferredLock
from twisted.internet.threads import deferToThread

from oauth2client.client import SignedJwtAssertionCredentials

from zope.interface import implements

from allmydata.util import log
from allmydata.node import InvalidValueError
from allmydata.storage.backends.cloud.cloud_common import IContainer, \
     CloudServiceError, ContainerItem, ContainerListing, ContainerRetryMixin, \
     HTTPClientMixin


def configure_googlestorage_container(*args):
    """
    Configure the Google Cloud Storage container.
    """


class AuthenticationClient(object):
    """
    Retrieve access tokens for the Google Storage API, using OAuth 2.0.

    See https://developers.google.com/accounts/docs/OAuth2ServiceAccount for
    more details.
    """

    def __init__(self, account_name, private_key, private_key_password='notasecret'):
        self.credentials = SignedJwtAssertionCredentials(
            account_name, private_key,
            "https://www.googleapis.com/auth/devstorage.read_write",
            private_key_password = private_key_password,
            )
        self._need_first_auth = True
        self._lock = DeferredLock()

    def get_authorization_header(self):
        """
        Return a Deferred that fires with the value to use for the
        Authorization header in HTTP requests.
        """
        def refreshIfNecessary():
            if self._need_first_auth or self.credentials.access_token_expired:
                self._need_first_auth = False
                return deferToThread(self.credentials.refresh, httplib2.Http())
        d = self._lock.run(refreshIfNecessary)

        def refreshed(ignore):
            headers = {}
            self.credentials.apply(headers)
            return headers['Authorization']
        d.addCallback(refreshed)
        return d


class GoogleStorageContainer(HTTPClientMixin, ContainerRetryMixin):
    implements(IContainer)

    USER_AGENT = "Tahoe-LAFS Google Storage client"

    def __init__(self, auth_client, bucket_name, override_reactor=None):
        pass


if __name__ == '__main__':
    from twisted.internet import reactor
    from twisted.web.client import getPage
    import sys
    auth = AuthenticationClient(sys.argv[1], file(sys.argv[2]).read())
    def println(result):
        print result
        reactor.stop()
    def gotAuth(value):
        return getPage("https://storage.googleapis.com/",
                       headers={"Authorization": value,
                                "x-goog-api-version": "2",
                                "x-goog-project-id": sys.argv[3]}).addCallback(println)
    auth.get_authorization_header().addCallback(gotAuth)
    reactor.run()

