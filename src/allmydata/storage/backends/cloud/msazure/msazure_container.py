"""
Storage backend using Microsoft Azure Blob Storage service.

See http://msdn.microsoft.com/en-us/library/windowsazure/dd179428.aspx for
details on the authentication scheme.
"""
import urlparse
import base64
import hmac
import hashlib
import urllib
try:
    from xml.etree import cElementTree as ElementTree
    ElementTree  # hush pyflakes
except ImportError:
    from xml.etree import ElementTree
import time

from zope.interface import implements

from twisted.web.http_headers import Headers
from twisted.web.http import datetimeToString

from allmydata.storage.backends.cloud.cloud_common import IContainer, \
     ContainerItem, ContainerListing, CommonContainerMixin


class MSAzureStorageContainer(CommonContainerMixin):
    implements(IContainer)

    USER_AGENT = "Tahoe-LAFS Microsoft Azure client"

    _time = time.time

    def __init__(self, account_name, account_key, container_name,
                 override_reactor=None):
        CommonContainerMixin.__init__(self, container_name, override_reactor)
        self._account_name = account_name
        self._account_key = base64.b64decode(account_key)
        self.URI = "https://%s.blob.core.windows.net" % (account_name, )

    def _calculate_presignature(self, method, url, headers):
        """
        Calculate the value to be signed for the given request information.

        We only implement a subset of the standard. In particular, we assume
        x-ms-date header has been provided, so don't include any Date header.

        The HMAC, and formatting into HTTP header, is not done in this layer.
        """
        headers = Headers(headers)
        parsed_url = urlparse.urlparse(url)
        result = method + "\n"
        # Add standard headers:
        for header in ['content-encoding', 'content-language',
                       'content-length', 'content-md5',
                       'content-type', 'date', 'if-modified-since',
                        'if-match', 'if-none-match',
                       'if-unmodified-since', 'range']:
            value = headers.getRawHeaders(header, [""])[0]
            if header == "date":
                value = ""
            result += value + "\n"

        # Add x-ms headers:
        x_ms_headers = []
        x_ms_date = False
        for name, values in headers.getAllRawHeaders():
            name = name.lower()
            if name.startswith("x-ms"):
                x_ms_headers.append("%s:%s" % (name, values[0]))
                if name == "x-ms-date":
                    x_ms_date = True
        x_ms_headers.sort()
        if x_ms_headers:
            result += "\n".join(x_ms_headers) + "\n"
        if not x_ms_date:
            raise ValueError("x-ms-date must be included")

        # Add path:
        result += "/%s%s" % (self._account_name, parsed_url.path)

        # Add query args:
        query_args = urlparse.parse_qs(parsed_url.query).items()
        query_args.sort()
        for name, value in query_args:
            result += "\n%s:%s" % (name, ",".join(value))
        return result

    def _calculate_signature(self, method, url, headers):
        """
        Calculate the signature for the given request information.

        This includes base64ing and HMACing.

        headers is a twisted.web.http_headers.Headers instance.

        The returned value is suitable for us as an Authorization header.
        """
        data = self._calculate_presignature(method, url, headers)
        signature = hmac.HMAC(self._account_key, data, hashlib.sha256).digest()
        return "SharedKey %s:%s" % (self._account_name, base64.b64encode(signature))

    def _authorized_http_request(self, what, method, url, request_headers,
                                 body=None, need_response_body=False):
        """
        Do an HTTP request with the addition of a authorization header.
        """
        request_headers["x-ms-date"] = [datetimeToString(self._time())]
        request_headers["x-ms-version"] = ["2012-02-12"]
        request_headers["Authorization"] = [
            self._calculate_signature(method, url, request_headers)]
        return self._http_request(what, method, url, request_headers, body=body,
                                  need_response_body=need_response_body)

    def _parse_item(self, element):
        """
        Parse a <Blob> XML element into a ContainerItem.
        """
        key = element.find("Name").text
        element = element.find("Properties")
        last_modified = element.find("Last-Modified").text
        etag = element.find("Etag").text
        size = int(element.find("Content-Length").text)
        storage_class = "STANDARD" # not sure what it means in this context
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

        root = ElementTree.fromstring(data)
        if root.tag != "EnumerationResults":
            raise ValueError("Unknown root XML element %s" % (root.tag,))
        for element in root:
            tag = element.tag
            if tag == "NextMarker":
                marker = element.text
            elif tag == "Blobs":
                for subelement in element:
                    if subelement.tag == "Blob":
                        contents.append(self._parse_item(subelement))

        return ContainerListing(name, prefix, marker, max_keys, is_truncated,
                                contents, common_prefixes)

    def _list_objects(self, prefix):
        """
        List objects in this container with the given prefix.
        """
        url = self._make_container_url(self.URI)
        url += "?comp=list&restype=container"
        if prefix:
            url += "&prefix=" + urllib.quote(prefix, safe='')
        d = self._authorized_http_request("MS Azure list objects", 'GET',
                                          url, {},
                                          body=None,
                                          need_response_body=True)
        d.addCallback(lambda (response, body): self._parse_list(body, prefix))
        return d

    def _put_object(self, object_name, data, content_type, metadata):
        """
        Put an object into this container.
        """
        url = self._make_object_url(self.URI, object_name)
        # In theory Agent will add the content length for us, but we need it
        # at this layer in order for the HMAC authorization to be calculated
        # correctly:
        request_headers = {'Content-Length': ["%d" % (len(data),)],
                           'Content-Type': [content_type],
                           "x-ms-blob-type": ["BlockBlob"],
                           }
        for key, value in metadata.items():
            request_headers["x-ms-meta-%s" % (key,)] = [value]

        d = self._authorized_http_request("MS Azure PUT object", 'PUT', url,
                                          request_headers,
                                          body=data, need_response_body=False)
        d.addCallback(lambda (response, body): body)
        return d

    def _get_object(self, object_name):
        """
        Get an object from this container.
        """
        url = self._make_object_url(self.URI, object_name)
        d = self._authorized_http_request("MS Azure GET object", 'GET',
                                          url, {},
                                          body=None,
                                          need_response_body=True)
        d.addCallback(lambda (response, body): body)
        return d

    def _delete_object(self, object_name):
        """
        Delete an object from this container.
        """
        url = self._make_object_url(self.URI, object_name)
        d = self._authorized_http_request("MS Azure DELETE object", 'DELETE',
                                          url, {},
                                          body=None,
                                          need_response_body=False)
        d.addCallback(lambda (response, body): body)
        return d


def configure_msazure_container(storedir, config):
    """
    Configure the MS Azure storage container.
    """
    account_name = config.get_config("storage", "msazure.account_name")
    container_name = config.get_config("storage", "msazure.container_name")
    account_key = config.get_private_config("msazure_account_key")
    return MSAzureStorageContainer(account_name, account_key, container_name)


if __name__ == '__main__':
    from twisted.internet import reactor, defer
    from twisted.python import log
    import sys
    msc = MSAzureStorageContainer(sys.argv[1], sys.argv[2], sys.argv[3])

    @defer.inlineCallbacks
    def testtransactions():
        yield msc.put_object("key", "the value")
        print "Uploaded 'key', with value 'the value'"
        print
        print "Get contents:",
        result = yield msc.list_objects()
        print [item.key for item in result.contents]
        print "Get key, value is:"
        print (yield msc.get_object("key"))
        print
        print "Delete item..."
        yield msc.delete_object("key")
        print
        print "Get contents:", 
        result = yield msc.list_objects()
        print [item.key for item in result.contents]
        reactor.stop()

    testtransactions().addErrback(log.err)
    reactor.run()
