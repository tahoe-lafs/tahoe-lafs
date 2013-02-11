
from twisted.internet import defer, reactor
from twisted.web.client import Agent
from twisted.web.http_headers import Headers

from zope.interface import implements

from allmydata.util import log
from allmydata.util.assertutil import _assert
from allmydata.node import InvalidValueError
from allmydata.storage.backends.cloud.cloud_common import IContainer, \
     ContainerRetryMixin, ContainerListMixin


DEFAULT_AUTH_URLS = {
    "rackspace": "https://identity.api.rackspacecloud.com/v1.0",
    "rackspace-uk": "https://lon.identity.api.rackspacecloud.com/v1.0",
}

def configure_openstack_container(storedir, config):
    api_key = config.get_or_create_private_config("openstack_api_key")
    provider = config.get_config("storage", "openstack.provider", "rackspace").lower()
    if provider not in DEFAULT_AUTH_URLS:
        raise InvalidValueError("[storage]openstack.provider %r is not recognized\n"
                                "Valid providers are: %s" % (provider, ", ".join(DEFAULT_AUTH_URLS.keys())))

    auth_service_url = config.get_config("storage", "openstack.url", DEFAULT_AUTH_URLS[provider])
    username = config.get_config("storage", "openstack.username")
    reauth_period = 23*60*60 #seconds

    auth_client = AuthenticationClient(api_key, provider, auth_service_url, username, reauth_period)
    return OpenStackContainer(auth_client)


class UnexpectedAuthenticationResponse(Exception):
    def __init__(self, msg, response_code, response_headers):
        Exception.__init__(self, msg)
        self.response_code = response_code
        self.response_headers = response_headers


class AuthenticationInfo(object):
    def __init__(self, storage_url, cdn_management_url, auth_token):
        self.storage_url = storage_url
        self.cdn_management_url = cdn_management_url
        self.auth_token = auth_token


class AuthenticationClient(object):
    """
    I implement a client for the Rackspace authentication service.
    It is not clear whether this is also implemented by other OpenStack providers.
    """
    def __init__(self, api_key, provider, auth_service_url, username, reauth_period, override_reactor=None):
        self._api_key = api_key
        self._auth_service_url = auth_service_url
        self._username = username
        self._reauth_period = reauth_period
        self._reactor = override_reactor or reactor
        self._agent = Agent(self._reactor)

        _assert(provider.startswith("rackspace"), provider=provider)
        self._authenticate = self._authenticate_to_rackspace

        # Not authorized yet.
        self._auth_info = None
        self._first_auth_lock = defer.DeferredLock()
        d = self.get_auth_info()
        d.addBoth(lambda ign: None)

    def get_auth_info(self):
        # It is intentional that this returns the previous auth_info while a reauthentication is in progress.
        if self._auth_info is not None:
            return defer.succeed(self._auth_info)
        else:
            return self.get_auth_info_locked()

    def get_auth_info_locked(self, suppress_errors=False):
        d = self._first_auth_lock.acquire()
        d.addCallback(self._authenticate)
        def _release(res):
            self._first_auth_lock.release()
            return res
        d.addBoth(_release)
        d.addCallback(lambda ign: self._auth_info)
        if suppress_errors:
            d.addErrback(lambda ign: self._auth_info)
        return d

    def _authenticate_to_rackspace(self, ign=None):
        # <http://docs.rackspace.com/files/api/v1/cf-devguide/content/Authentication-d1e639.html>

        # Agent.request adds a Host header automatically based on the URL.
        request_headers = {
            'User-Agent': ['Tahoe-LAFS authentication client'],
            'X-Auth-User': [self._username],
            'X-Auth-Key': [self._api_key],
        }
        log.msg("GET %s %r" % (self._auth_service_url, request_headers))
        d = defer.succeed(None)
        d.addCallback(lambda ign: self._agent.request('GET', self._auth_service_url, Headers(request_headers), None))

        def _got_response(response):
            log.msg("OpenStack auth response: %r %s" % (response.code, response.phrase))
            # "any 2xx response is a good response"
            if response.code < 200 or response.code >= 300:
                raise UnexpectedAuthenticationResponse("unexpected response code %r %s" % (response.code, response.phrase),
                                                       response.code, response.headers)

            def _get_header(name):
                hs = response.headers.getRawHeaders(name)
                if len(hs) == 0:
                    raise UnexpectedAuthenticationResponse("missing response header %r" % (name,),
                                                           response.code, response.headers)
                return hs[0]

            storage_url = _get_header('X-Storage-Url')
            cdn_management_url = _get_header('X-CDN-Management-Url')
            auth_token = _get_header('X-Auth-Token')
            # Don't log this unless debugging, since auth_token is a secret.
            #log.msg("Auth response is %s %s %s" % (storage_url, cdn_management_url, auth_token))
            self._auth_info = AuthenticationInfo(storage_url, cdn_management_url, auth_token)

            self._reactor.callLater(self._reauth_period, self.get_auth_info_locked, suppress_errors=True)
        d.addCallback(_got_response)
        def _failed(f):
            self._auth_info = None
            # do we need to retry?
            log.err(f)
            return f
        d.addErrback(_failed)
        return d


class OpenStackContainer(ContainerRetryMixin, ContainerListMixin):
    implements(IContainer)
    """
    I represent a real OpenStack container.
    """

    def __init__(self, auth_client):
        self._auth_client = auth_client

        #self.client = OpenStackClient(auth_client)
        #self.ServiceError = OpenStackError

    def __repr__(self):
        return ("<%s>" % (self.__class__.__name__,))

    def create(self):
        return self._do_request('create bucket', self.client.create, self.container_name)

    def delete(self):
        return self._do_request('delete bucket', self.client.delete, self.container_name)

    def list_some_objects(self, **kwargs):
        return self._do_request('list objects', self.client.get_bucket, self.container_name, **kwargs)

    def put_object(self, object_name, data, content_type='application/octet-stream', metadata={}):
        return self._do_request('PUT object', self.client.put_object, self.container_name,
                                object_name, data, content_type, metadata)

    def get_object(self, object_name):
        return self._do_request('GET object', self.client.get_object, self.container_name, object_name)

    def head_object(self, object_name):
        return self._do_request('HEAD object', self.client.head_object, self.container_name, object_name)

    def delete_object(self, object_name):
        return self._do_request('DELETE object', self.client.delete_object, self.container_name, object_name)

    def put_policy(self, policy):
        """
        Set access control policy on a bucket.
        """
        query = self.client.query_factory(
            action='PUT', creds=self.client.creds, endpoint=self.client.endpoint,
            bucket=self.container_name, object_name='?policy', data=policy)
        return self._do_request('PUT policy', query.submit)

    def get_policy(self):
        query = self.client.query_factory(
            action='GET', creds=self.client.creds, endpoint=self.client.endpoint,
            bucket=self.container_name, object_name='?policy')
        return self._do_request('GET policy', query.submit)

    def delete_policy(self):
        query = self.client.query_factory(
            action='DELETE', creds=self.client.creds, endpoint=self.client.endpoint,
            bucket=self.container_name, object_name='?policy')
        return self._do_request('DELETE policy', query.submit)
