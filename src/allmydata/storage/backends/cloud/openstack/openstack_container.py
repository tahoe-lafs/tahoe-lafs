
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
        log.msg(format="OpenStack auth GET %(url)s %(headers)s",
                url=self._auth_service_url, headers=repr(request_headers), level=log.OPERATIONAL)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self._agent.request('GET', self._auth_service_url, Headers(request_headers), None))

        def _got_response(response):
            log.msg(format="OpenStack auth response: %(code)d %(phrase)s",
                    code=response.code, phrase=response.phrase, level=log.OPERATIONAL)
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

    USER_AGENT = 'Tahoe-LAFS OpenStack client'

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
                'User-Agent': [self.USER_AGENT],
                'X-Auth-Token': [auth_info.auth_token],
            }
            url = self._make_container_url(auth_info)
            if prefix:
                url += "?format=json&prefix=%s" % (urllib.quote(prefix, safe=''),)
            log.msg(format="OpenStack list GET %(url)s", url=url, level=log.OPERATIONAL)
            return self._agent.request('GET', url, Headers(request_headers), None)
        d.addCallback(_do_list)
        def _got_list_response(response):
            log.msg(format="OpenStack list GET response: %(code)d %(phrase)s",
                    code=response.code, phrase=response.phrase, level=log.OPERATIONAL)
            if response.code < 200 or response.code >= 300:
                raise self.ServiceError("unexpected response code %r %s" % (response.code, response.phrase),
                                        response.code, response.headers)

            collector = DataCollector()
            response.deliverBody(collector)
            return collector.when_done()
        d.addCallback(_got_list_response)
        def _parse_list(json):
            items = simplejson.loads(json)
            log.msg(format="OpenStack list GET read %(length)d bytes, parsed as %(items)d items",
                    length=len(json), items=len(items), level=log.OPERATIONAL)

            def _make_containeritem(item):
                try:
                    key = item['name']
                    size = item['bytes']
                    modification_date = item['last_modified']
                    etag = item['hash']
                    storage_class = 'STANDARD'
                except KeyError, e:
                    raise self.ServiceError(str(e))
                else:
                    return ContainerItem(key, modification_date, etag, size, storage_class)

            contents = map(_make_containeritem, items)
            return ContainerListing(self._container_name, prefix, None, 10000, "false", contents=contents)
        d.addCallback(_parse_list)
        return d

    def _put_object(self, object_name, data, content_type='application/octet-stream', metadata={}):
        """
        Put an object in this bucket.
        Any existing object of the same name will be replaced.
        """
        d = self._auth_client.get_auth_info()
        def _do_put(auth_info):
            content_length = len(data)
            request_headers = {
                'User-Agent': [self.USER_AGENT],
                'X-Auth-Token': [auth_info.auth_token],
                'Content-Type': [content_type],
                'Content-Length': [content_length],
            }
            producer = FileBodyProducer(StringIO(data))
            url = self._make_object_url(auth_info, object_name)
            log.msg(format="OpenStack PUT %(url)s %(content_length)d",
                    url=url, content_length=content_length, level=log.OPERATIONAL)
            return self._agent.request('PUT', url, Headers(request_headers), producer)
        d.addCallback(_do_put)
        def _got_put_response(response):
            log.msg(format="OpenStack PUT response: %(code)d %(phrase)s",
                    code=response.code, phrase=response.phrase, level=log.OPERATIONAL)
            if response.code < 200 or response.code >= 300:
                raise self.ServiceError("unexpected response code %r %s" % (response.code, response.phrase),
                                        response.code, response.headers)
            response.deliverBody(Discard())
        d.addCallback(_got_put_response)
        return d

    def _get_object(self, object_name):
        """
        Get an object from this container.
        """
        d = self._auth_client.get_auth_info()
        def _do_get(auth_info):
            request_headers = {
                'User-Agent': [self.USER_AGENT],
                'X-Auth-Token': [auth_info.auth_token],
            }
            url = self._make_object_url(auth_info, object_name)
            log.msg(format="OpenStack GET %(url)s", url=url, level=log.OPERATIONAL)
            return self._agent.request('GET', url, Headers(request_headers), None)
        d.addCallback(_do_get)
        def _got_get_response(response):
            log.msg(format="OpenStack GET response: %(code)d %(phrase)s",
                    code=response.code, phrase=response.phrase, level=log.OPERATIONAL)
            if response.code < 200 or response.code >= 300:
                raise self.ServiceError("unexpected response code %r %s" % (response.code, response.phrase),
                                        response.code, response.headers)

            collector = DataCollector()
            response.deliverBody(collector)
            return collector.when_done()
        d.addCallback(_got_get_response)
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
                'User-Agent': [self.USER_AGENT],
                'X-Auth-Token': [auth_info.auth_token],
            }
            url = self._make_object_url(auth_info, object_name)
            log.msg(format="OpenStack DELETE %(url)s", url=url, level=log.OPERATIONAL)
            return self._agent.request('DELETE', url, Headers(request_headers), None)
        d.addCallback(_do_delete)
        def _got_delete_response(response):
            log.msg(format="OpenStack DELETE response: %(code)d %(phrase)s",
                    code=response.code, phrase=response.phrase, level=log.OPERATIONAL)
            if response.code < 200 or response.code >= 300:
                raise self.ServiceError("unexpected response code %r %s" % (response.code, response.phrase),
                                        response.code, response.headers)
            response.deliverBody(Discard())
        d.addCallback(_got_delete_response)
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
