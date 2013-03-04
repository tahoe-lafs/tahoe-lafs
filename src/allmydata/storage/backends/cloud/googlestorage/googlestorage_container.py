"""
This requires the oauth2client library:

http://code.google.com/p/google-api-python-client/downloads/list
"""

import httplib2

from twisted.internet.defer import DeferredLock, maybeDeferred
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

    def __init__(self, account_name, private_key, private_key_password='notasecret',
                 _credentialsClass=SignedJwtAssertionCredentials,
                 _deferToThread=deferToThread):
        # Google ships pkcs12 private keys encrypted with "notasecret" as the
        # password. In order for automated running to work we'd need to
        # include the password in the config file, so it adds no extra
        # security even if someone chooses a different password. So it's seems
        # simplest to hardcode it for now and it'll work with unmodified
        # private keys issued by Google.
        self._credentials = _credentialsClass(
            account_name, private_key,
            "https://www.googleapis.com/auth/devstorage.read_write",
            private_key_password = private_key_password,
            )
        self._deferToThread = _deferToThread
        self._need_first_auth = True
        self._lock = DeferredLock()
        # Get initial token:
        self._refresh_if_necessary(force=True)

    def _refresh_if_necessary(self, force=False):
        """
        Get a new authorization token, if necessary.
        """
        def run():
            if force or self._credentials.access_token_expired:
                # Generally using a task-specific thread pool is better than using
                # the reactor one. However, this particular call will only run
                # once an hour, so it's not likely to tie up all the threads.
                return self._deferToThread(self._credentials.refresh, httplib2.Http())
        return self._lock.run(run)

    def get_authorization_header(self):
        """
        Return a Deferred that fires with the value to use for the
        Authorization header in HTTP requests.
        """
        d = self._refresh_if_necessary()

        def refreshed(ignore):
            headers = {}
            self._credentials.apply(headers)
            return headers['Authorization']
        d.addCallback(refreshed)
        return d


class GoogleStorageContainer(HTTPClientMixin, ContainerRetryMixin):
    implements(IContainer)

    USER_AGENT = "Tahoe-LAFS Google Storage client"

    def __init__(self, auth_client, project_id, bucket_name, override_reactor=None):
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

