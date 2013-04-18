"""
This requires the oauth2client library:

http://code.google.com/p/google-api-python-client/downloads/list
"""

import urllib
try:
    from xml.etree import cElementTree as ElementTree
    ElementTree  # hush pyflakes
except ImportError:
    from xml.etree import ElementTree

# Maybe we can make a thing that looks like httplib2.Http but actually uses
# Twisted?
import httplib2

from twisted.internet.defer import DeferredLock
from twisted.internet.threads import deferToThread
from twisted.web.http import UNAUTHORIZED

try:
    from oauth2client.client import SignedJwtAssertionCredentials
    SignedJwtAssertionCredentials  # hush pyflakes
    oauth2client_available = True
except ImportError:
    oauth2client_available = False
    SignedJwtAssertionCredentials = None

from zope.interface import implements

from allmydata.storage.backends.cloud.cloud_common import IContainer, \
     ContainerItem, ContainerListing, CommonContainerMixin


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


class GoogleStorageContainer(CommonContainerMixin):
    implements(IContainer)

    USER_AGENT = "Tahoe-LAFS Google Storage client"
    URI = "https://storage.googleapis.com"
    NAMESPACE = "{http://doc.s3.amazonaws.com/2006-03-01}"
    # I can't get Google to actually use their own namespace?!
    #NAMESPACE="{http://doc.storage.googleapis.com/2010-04-03}"

    def __init__(self, auth_client, project_id, bucket_name, override_reactor=None):
        CommonContainerMixin.__init__(self, bucket_name, override_reactor)
        self._auth_client = auth_client
        self._project_id = project_id # Only need for bucket creation/deletion

    def _react_to_error(self, response_code):
        if response_code == UNAUTHORIZED:
            return True
        else:
            return CommonContainerMixin._react_to_error(self, response_code)

    def _get_object(self, object_name):
        """
        Get an object from this container.
        """
        d = self._auth_client.get_authorization_header()
        def _do_get(auth_header):
            request_headers = {
                'Authorization': [auth_header],
                "x-goog-api-version": ["2"],
            }
            url = self._make_object_url(self.URI, object_name)
            return self._http_request("Google Storage GET object", 'GET', url, request_headers,
                                      body=None,
                                      need_response_body=True)
        d.addCallback(_do_get)
        d.addCallback(lambda (response, body): body)
        return d

    def _delete_object(self, object_name):
        """
        Delete an object from this container.
        """
        d = self._auth_client.get_authorization_header()
        def _do_delete(auth_header):
            request_headers = {
                'Authorization': [auth_header],
                "x-goog-api-version": ["2"],
            }
            url = self._make_object_url(self.URI, object_name)
            return self._http_request("Google Storage DELETE object", 'DELETE', url, request_headers,
                                      body=None,
                                      need_response_body=False)
        d.addCallback(_do_delete)
        d.addCallback(lambda (response, body): body)
        return d

    def _put_object(self, object_name, data, content_type, metadata):
        """
        Put an object into this container.
        """
        d = self._auth_client.get_authorization_header()
        def _do_put(auth_header):
            request_headers = {
                'Authorization': [auth_header],
                "x-goog-api-version": ["2"],
                "Content-Type": [content_type],
            }
            for key, value in metadata.items():
                request_headers["x-goog-meta-" + key] = [value]
            url = self._make_object_url(self.URI, object_name)
            return self._http_request("Google Storage PUT object", 'PUT', url, request_headers,
                                      body=data,
                                      need_response_body=False)
        d.addCallback(_do_put)
        d.addCallback(lambda (response, body): body)
        return d

    def _parse_item(self, element):
        """
        Parse a <Contents> XML element into a ContainerItem.
        """
        key = element.find(self.NAMESPACE + "Key").text
        last_modified = element.find(self.NAMESPACE + "LastModified").text
        etag = element.find(self.NAMESPACE + "ETag").text
        size = int(element.find(self.NAMESPACE + "Size").text)
        storage_class = element.find(self.NAMESPACE + "StorageClass")
        storage_class = "STANDARD"
        owner = None # Don't bother parsing this at the moment

        return ContainerItem(key, last_modified, etag, size, storage_class,
                             owner)

    def _parse_list(self, data, prefix):
        """
        Parse the XML response, converting it into a ContainerListing.
        """
        name = self._container_name
        marker = None
        max_keys = None
        is_truncated = "false"
        common_prefixes = []
        contents = []

        # Sigh.
        ns_len = len(self.NAMESPACE)

        root = ElementTree.fromstring(data)
        if root.tag != self.NAMESPACE + "ListBucketResult":
            raise ValueError("Unknown root XML element %s" % (root.tag,))
        for element in root:
            tag = element.tag[ns_len:]
            if tag == "Marker":
                marker = element.text
            elif tag == "IsTruncated":
                is_truncated = element.text
            elif tag == "Contents":
                contents.append(self._parse_item(element))
            elif tag == "CommonPrefixes":
                common_prefixes.append(element.find(self.NAMESPACE + "Prefix").text)

        return ContainerListing(name, prefix, marker, max_keys, is_truncated,
                                contents, common_prefixes)

    def _list_objects(self, prefix):
        """
        List objects in this container with the given prefix.
        """
        d = self._auth_client.get_authorization_header()
        def _do_list(auth_header):
            request_headers = {
                'Authorization': [auth_header],
                "x-goog-api-version": ["2"],
                "x-goog-project-id": [self._project_id],
            }
            url = self._make_container_url(self.URI)
            url += "?prefix=" + urllib.quote(prefix, safe='')
            return self._http_request("Google Storage list objects", 'GET', url, request_headers,
                                      body=None,
                                      need_response_body=True)
        d.addCallback(_do_list)
        d.addCallback(lambda (response, body): self._parse_list(body, prefix))
        return d


def configure_googlestorage_container(storedir, config):
    """
    Configure the Google Cloud Storage container.
    """
    account_email = config.get_config("storage", "googlestorage.account_email")
    private_key = config.get_private_config("googlestorage_private_key")
    bucket_name = config.get_config("storage", "googlestorage.bucket")
    # Only necessary if we do bucket creation/deletion, otherwise can be
    # removed:
    project_id = config.get_config("storage", "googlestorage.project_id")

    authclient = AuthenticationClient(account_email, private_key)
    return GoogleStorageContainer(authclient, project_id, bucket_name)


if __name__ == '__main__':
    from twisted.internet import reactor
    import sys
    auth = AuthenticationClient(sys.argv[1], file(sys.argv[2]).read())
    gsc = GoogleStorageContainer(auth, sys.argv[3], sys.argv[4])
    def println(result):
        for item in result.contents:
            print "Bucket has key", item.key
        reactor.stop()
    def gotAuth(value):
        gsc.list_objects().addCallback(println)
    auth.get_authorization_header().addCallback(gotAuth)
    reactor.run()
