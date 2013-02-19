
import urllib, simplejson
from cStringIO import StringIO
from collections import deque

from twisted.internet import defer, reactor
from allmydata.util.deferredutil import eventually_callback, eventually_errback

from twisted.internet.protocol import Protocol
from twisted.web.client import Agent, FileBodyProducer, ResponseDone
from twisted.web.http_headers import Headers

from zope.interface import implements

from allmydata.util import log
from allmydata.util.assertutil import _assert
from allmydata.node import InvalidValueError
from allmydata.storage.backends.cloud.cloud_common import IContainer, \
     CloudServiceError, ContainerItem, ContainerListing, ContainerRetryMixin


# Enabling this will cause secrets to be logged.
UNSAFE_DEBUG = False


DEFAULT_AUTH_URLS = {
    "rackspace.com": "https://identity.api.rackspacecloud.com/v1.0",
    "rackspace.co.uk": "https://lon.identity.api.rackspacecloud.com/v1.0",
}

USER_AGENT = "Tahoe-LAFS OpenStack client"

def configure_openstack_container(storedir, config):
    api_key = config.get_or_create_private_config("openstack_api_key")
    provider = config.get_config("storage", "openstack.provider", "rackspace.com").lower()
    if provider not in DEFAULT_AUTH_URLS:
        raise InvalidValueError("[storage]openstack.provider %r is not recognized\n"
                                "Valid providers are: %s" % (provider, ", ".join(DEFAULT_AUTH_URLS.keys())))

    auth_service_url = config.get_config("storage", "openstack.url", DEFAULT_AUTH_URLS[provider])
    username = config.get_config("storage", "openstack.username")
    container_name = config.get_config("storage", "openstack.container")
    reauth_period = 23*60*60 #seconds

    auth_client = AuthenticationClient(api_key, provider, auth_service_url, username, reauth_period)
    return OpenStackContainer(auth_client, container_name)


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


def _http_request(what, agent, method, url, request_headers, body=None, need_response_body=False):
    # Agent.request adds a Host header automatically based on the URL.
    request_headers['User-Agent'] = [USER_AGENT]

    if body is None:
        bodyProducer = None
    else:
        bodyProducer = FileBodyProducer(StringIO(body))
        # We don't need to explicitly set Content-Length because FileBodyProducer knows the length
        # (and if we do it won't work, because in that case Content-Length would be duplicated).

    log.msg(format="OpenStack %(what)s request %(method)s %(url)s %(headers)s",
            what=what, method=method, url=url, headers=repr(request_headers), level=log.OPERATIONAL)

    d = defer.maybeDeferred(agent.request, method, url, Headers(request_headers), bodyProducer)

    def _got_response(response):
        log.msg(format="OpenStack %(what)s response: %(code)d %(phrase)s",
                what=what, code=response.code, phrase=response.phrase, level=log.OPERATIONAL)

        if response.code < 200 or response.code >= 300:
            raise CloudServiceError(None, response.code,
                                    message="unexpected response code %r %s" % (response.code, response.phrase))

        if need_response_body:
            collector = DataCollector()
            response.deliverBody(collector)
            d2 = collector.when_done()
            d2.addCallback(lambda body: (response, body))
            return d2
        else:
            response.deliverBody(Discard())
            return (response, None)
    d.addCallback(_got_response)
    return d

def _get_header(response, name):
    hs = response.headers.getRawHeaders(name)
    if len(hs) == 0:
        raise CloudServiceError(None, response.code,
                                message="missing response header %r" % (name,))
    return hs[0]


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
        self._delayed = None

        _assert(provider.startswith("rackspace"), provider=provider)
        self._authenticate = self._authenticate_to_rackspace

        # Not authorized yet.
        self._auth_info = None
        self._auth_lock = defer.DeferredLock()
        d = self.get_auth_info()
        d.addBoth(lambda ign: None)

    def get_auth_info(self):
        # It is intentional that this returns the previous auth_info while a reauthentication is in progress.
        if self._auth_info is not None:
            return defer.succeed(self._auth_info)
        else:
            return self.get_auth_info_locked()

    def get_auth_info_locked(self, suppress_errors=False):
        d = self._auth_lock.run(self._authenticate)
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
        log.msg(format="OpenStack auth GET %(url)s %(headers)s",
                url=self._auth_service_url, headers=repr(request_headers), level=log.OPERATIONAL)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self._agent.request('GET', self._auth_service_url, Headers(request_headers), None))

        def _got_response(response):
            log.msg(format="OpenStack auth response: %(code)d %(phrase)s",
                    code=response.code, phrase=response.phrase, level=log.OPERATIONAL)
            _check_response_code(response)

            def _get_header(name):
                hs = response.headers.getRawHeaders(name)
                if len(hs) == 0:
                    raise UnexpectedAuthenticationResponse("missing response header %r" % (name,),
                                                           response.code, response.headers)
                return hs[0]

            storage_url = _get_header('X-Storage-Url')
            cdn_management_url = _get_header('X-CDN-Management-Url')
            auth_token = _get_header('X-Auth-Token')
            if UNSAFE_DEBUG:
                print "Auth response is %s %s %s" % (storage_url, cdn_management_url, auth_token)
            self._auth_info = AuthenticationInfo(storage_url, cdn_management_url, auth_token)

            self._delayed = self._reactor.callLater(self._reauth_period, self.get_auth_info_locked, suppress_errors=True)
        d.addCallback(_got_response)
        def _failed(f):
            self._auth_info = None
            # do we need to retry?
            log.err(f)
            return f
        d.addErrback(_failed)
        return d

    def shutdown(self):
        """Used by unit tests to avoid unclean reactor errors."""
        if self._delayed:
            self._delayed.cancel()


class Discard(Protocol):
    # see http://twistedmatrix.com/trac/ticket/5488
    def makeConnection(self, producer):
        producer.stopProducing()


class DataCollector(Protocol):
    def __init__(self):
        self._data = deque()
        self._done = defer.Deferred()

    def dataReceived(self, bytes):
        self._data.append(bytes)

    def connectionLost(self, reason):
        if reason.check(ResponseDone):
            eventually_callback(self._done)("".join(self._data))
        else:
            def _failed(): raise CloudServiceError(reason.getErrorMessage())
            eventually_errback(self._done)(defer.execute(_failed))

    def when_done(self):
        """CAUTION: this always returns the same Deferred."""
        return self._done


class OpenStackContainer(ContainerRetryMixin):
    implements(IContainer)

    def __init__(self, auth_client, container_name, override_reactor=None):
        self._auth_client = auth_client
        self._container_name = container_name
        self._reactor = override_reactor or reactor
        self._agent = Agent(self._reactor)
        self.ServiceError = CloudServiceError

    def __repr__(self):
        return ("<%s %r>" % (self.__class__.__name__, self._container_name,))

    def _make_container_url(self, auth_info):
        return "%s/%s" % (auth_info.storage_url, urllib.quote(self._container_name, safe=''))

    def _make_object_url(self, auth_info, object_name):
        return "%s/%s/%s" % (auth_info.storage_url, urllib.quote(self._container_name, safe=''), urllib.quote(object_name))

    def _create(self):
        """
        Create this container.
        """
        raise NotImplementedError

    def _delete(self):
        """
        Delete this container.
        The cloud service may require the container to be empty before it can be deleted.
        """
        raise NotImplementedError

    def _list_objects(self, prefix=''):
        """
        Get a ContainerListing that lists objects in this container.

        prefix: (str) limit the returned keys to those starting with prefix.
        """
        d = self._auth_client.get_auth_info()
        def _do_list(auth_info):
            request_headers = {
                'X-Auth-Token': [auth_info.auth_token],
            }
            url = self._make_container_url(auth_info)
            if prefix:
                url += "?format=json&prefix=%s" % (urllib.quote(prefix, safe=''),)
            return _http_request("list objects", self._agent, 'GET', url, request_headers, None, need_response_body=True)
        d.addCallback(_do_list)
        d.addCallback(lambda (response, json): self._parse_list(response, json, prefix))
        return d

    def _parse_list(self, response, json, prefix):
        try:
            items = simplejson.loads(json)
        except simplejson.decoder.JSONDecodeError, e:
            raise self.ServiceError(None, response.code,
                                    message="could not decode list response: %s" % (e,))

        log.msg(format="OpenStack list read %(length)d bytes, parsed as %(items)d items",
                length=len(json), items=len(items), level=log.OPERATIONAL)

        def _make_containeritem(item):
            try:
                key = item['name']
                size = item['bytes']
                modification_date = item['last_modified']
                etag = item['hash']
                storage_class = 'STANDARD'
            except KeyError, e:
                raise self.ServiceError(None, response.code,
                                        message="missing field in list response: %s" % (e,))
            else:
                return ContainerItem(key, modification_date, etag, size, storage_class)

        contents = map(_make_containeritem, items)
        return ContainerListing(self._container_name, prefix, None, 10000, "false", contents=contents)

    def _put_object(self, object_name, data, content_type='application/octet-stream', metadata={}):
        """
        Put an object in this bucket.
        Any existing object of the same name will be replaced.
        """
        d = self._auth_client.get_auth_info()
        def _do_put(auth_info):
            request_headers = {
                'X-Auth-Token': [auth_info.auth_token],
                'Content-Type': [content_type],
            }
            url = self._make_object_url(auth_info, object_name)
            return _http_request("put object", self._agent, 'PUT', url, request_headers, data)
        d.addCallback(_do_put)
        d.addCallback(lambda ign: None)
        return d

    def _get_object(self, object_name):
        """
        Get an object from this container.
        """
        d = self._auth_client.get_auth_info()
        def _do_get(auth_info):
            request_headers = {
                'X-Auth-Token': [auth_info.auth_token],
            }
            url = self._make_object_url(auth_info, object_name)
            return _http_request("get object", self._agent, 'GET', url, request_headers, need_response_body=True)
        d.addCallback(_do_get)
        d.addCallback(lambda (response, body): body)
        return d

    def _head_object(self, object_name):
        """
        Retrieve object metadata only.
        """
        raise NotImplementedError

    def _delete_object(self, object_name):
        """
        Delete an object from this container.
        Once deleted, there is no method to restore or undelete an object.
        """
        d = self._auth_client.get_auth_info()
        def _do_delete(auth_info):
            request_headers = {
                'X-Auth-Token': [auth_info.auth_token],
            }
            url = self._make_object_url(auth_info, object_name)
            return _http_request("delete", self._agent, 'DELETE', url, request_headers)
        d.addCallback(_do_delete)
        d.addCallback(lambda ign: None)
        return d

    def create(self):
        return self._do_request('create container', self._create)

    def delete(self):
        return self._do_request('delete container', self._delete)

    def list_objects(self, prefix=''):
        return self._do_request('list objects', self._list_objects, prefix)

    def put_object(self, object_name, data, content_type='application/octet-stream', metadata={}):
        return self._do_request('PUT object', self._put_object, object_name, data, content_type, metadata)

    def get_object(self, object_name):
        return self._do_request('GET object', self._get_object, object_name)

    def head_object(self, object_name):
        return self._do_request('HEAD object', self._head_object, object_name)

    def delete_object(self, object_name):
        return self._do_request('DELETE object', self._delete_object, object_name)
