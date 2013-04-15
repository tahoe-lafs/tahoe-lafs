"""
Storage backend using Microsoft Azure Blob Storage service.

See http://msdn.microsoft.com/en-us/library/windowsazure/dd179428.aspx for
details on the authentication scheme.
"""
import urlparse
import base64
import hmac
import hashlib

from zope.interface import implements

from allmydata.storage.backends.cloud.cloud_common import IContainer, \
     ContainerItem, ContainerListing, CommonContainerMixin

def configure_msazure_container(*args):
    pass


class MSAzureStorageContainer(CommonContainerMixin):
    implements(IContainer)

    USER_AGENT = "Tahoe-LAFS Microsoft Azure client"

    def __init__(self, account_name, account_key, container_name,
                 override_reactor=None):
        CommonContainerMixin.__init__(self, container_name, override_reactor)
        self._account_name = account_name
        self._account_key = base64.b64decode(account_key)

    def _calculate_presignature(self, method, url, headers):
        """
        Calculate the value to be signed for the given request information.

        We only implement a subset of the standard. In particular, we assume
        x-ms-date header has been provided, so don't include any Date header.

        The HMAC, and formatting into HTTP header, is not done in this layer.
        """
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
