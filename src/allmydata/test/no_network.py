"""
This contains a test harness that creates a full Tahoe grid in a single
process (actually in a single MultiService) which does not use the network.
It does not use an Introducer, and there are no foolscap Tubs. Each storage
server puts real shares on disk, but is accessed through loopback
RemoteReferences instead of over serialized SSL. It is not as complete as
the common.SystemTestMixin framework (which does use the network), but
should be considerably faster: on my laptop, it takes 50-80ms to start up,
whereas SystemTestMixin takes close to 2s.

This should be useful for tests which want to examine and/or manipulate the
uploaded shares, checker/verifier/repairer tests, etc. The clients have no
Tubs, so it is not useful for tests that involve a Helper.
"""

from __future__ import annotations

from six import ensure_text

from typing import Callable

import os
from base64 import b32encode
from functools import (
    partial,
)
from zope.interface import implementer
from twisted.application import service
from twisted.internet import defer
from twisted.python.failure import Failure
from twisted.web.error import Error
from foolscap.api import Referenceable, fireEventually, RemoteException
from foolscap.ipb import (
    IRemoteReference,
)
import treq

from allmydata.util.assertutil import _assert

from allmydata import uri as tahoe_uri
from allmydata.client import _Client
from allmydata.storage.server import (
    StorageServer, storage_index_to_dir, FoolscapStorageServer,
)
from allmydata.util import fileutil, idlib, hashutil
from allmydata.util.hashutil import permute_server_hash
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.interfaces import IStorageBroker, IServer
from allmydata.storage_client import (
    _StorageServer,
)
from .common import (
    SameProcessStreamEndpointAssigner,
)


class IntentionalError(Exception):
    pass

class Marker(object):
    pass

fireNow = partial(defer.succeed, None)

@implementer(IRemoteReference)  # type: ignore  # warner/foolscap#79
class LocalWrapper(object):
    """
    A ``LocalWrapper`` presents the remote reference interface to a local
    object which implements a ``RemoteInterface``.
    """
    def __init__(self, original, fireEventually=fireEventually):
        """
        :param Callable[[], Deferred[None]] fireEventually: Get a Deferred
            that will fire at some point.  This is used to control when
            ``callRemote`` calls the remote method.  The default value allows
            the reactor to iterate before the call happens.  Use ``fireNow``
            to call the remote method synchronously.
        """
        self.original = original
        self.broken = False
        self.hung_until = None
        self.post_call_notifier = None
        self.disconnectors = {}
        self.counter_by_methname = {}
        self._fireEventually = fireEventually

    def _clear_counters(self):
        self.counter_by_methname = {}

    def callRemoteOnly(self, methname, *args, **kwargs):
        d = self.callRemote(methname, *args, **kwargs)
        del d # explicitly ignored
        return None

    def callRemote(self, methname, *args, **kwargs):
        # this is ideally a Membrane, but that's too hard. We do a shallow
        # wrapping of inbound arguments, and per-methodname wrapping of
        # selected return values.
        def wrap(a):
            if isinstance(a, Referenceable):
                return self._wrap(a)
            else:
                return a
        args = tuple([wrap(a) for a in args])
        kwargs = dict([(k,wrap(kwargs[k])) for k in kwargs])

        def _really_call():
            def incr(d, k): d[k] = d.setdefault(k, 0) + 1
            incr(self.counter_by_methname, methname)
            meth = getattr(self.original, "remote_" + methname)
            return meth(*args, **kwargs)

        def _call():
            if self.broken:
                if self.broken is not True: # a counter, not boolean
                    self.broken -= 1
                raise IntentionalError("I was asked to break")
            if self.hung_until:
                d2 = defer.Deferred()
                self.hung_until.addCallback(lambda ign: _really_call())
                self.hung_until.addCallback(lambda res: d2.callback(res))
                def _err(res):
                    d2.errback(res)
                    return res
                self.hung_until.addErrback(_err)
                return d2
            return _really_call()

        d = self._fireEventually()
        d.addCallback(lambda res: _call())
        def _wrap_exception(f):
            return Failure(RemoteException(f))
        d.addErrback(_wrap_exception)
        def _return_membrane(res):
            # rather than complete the difficult task of building a
            # fully-general Membrane (which would locate all Referenceable
            # objects that cross the simulated wire and replace them with
            # wrappers), we special-case certain methods that we happen to
            # know will return Referenceables.
            if methname == "allocate_buckets":
                (alreadygot, allocated) = res
                for shnum in allocated:
                    allocated[shnum] = self._wrap(allocated[shnum])
            if methname == "get_buckets":
                for shnum in res:
                    res[shnum] = self._wrap(res[shnum])
            return res
        d.addCallback(_return_membrane)
        if self.post_call_notifier:
            d.addCallback(self.post_call_notifier, self, methname)
        return d

    def notifyOnDisconnect(self, f, *args, **kwargs):
        m = Marker()
        self.disconnectors[m] = (f, args, kwargs)
        return m
    def dontNotifyOnDisconnect(self, marker):
        del self.disconnectors[marker]

    def _wrap(self, value):
        return LocalWrapper(value, self._fireEventually)


def wrap_storage_server(original):
    # Much of the upload/download code uses rref.version (which normally
    # comes from rrefutil.add_version_to_remote_reference). To avoid using a
    # network, we want a LocalWrapper here. Try to satisfy all these
    # constraints at the same time.
    wrapper = LocalWrapper(original)
    wrapper.version = original.remote_get_version()
    return wrapper

@implementer(IServer)
class NoNetworkServer(object):
    def __init__(self, serverid, rref):
        self.serverid = serverid
        self.rref = rref
    def __repr__(self):
        return "<NoNetworkServer for %s>" % self.get_name()
    # Special method used by copy.copy() and copy.deepcopy(). When those are
    # used in allmydata.immutable.filenode to copy CheckResults during
    # repair, we want it to treat the IServer instances as singletons.
    def __copy__(self):
        return self
    def __deepcopy__(self, memodict):
        return self

    def upload_permitted(self):
        return True

    def get_serverid(self):
        return self.serverid
    def get_permutation_seed(self):
        return self.serverid
    def get_lease_seed(self):
        return self.serverid
    def get_foolscap_write_enabler_seed(self):
        return self.serverid

    def get_name(self):
        # Other implementations return bytes.
        return idlib.shortnodeid_b2a(self.serverid).encode("utf-8")
    def get_longname(self):
        return idlib.nodeid_b2a(self.serverid)
    def get_nickname(self):
        return "nickname"
    def get_rref(self):
        return self.rref
    def get_storage_server(self):
        if self.rref is None:
            return None
        return _StorageServer(lambda: self.rref)
    def get_version(self):
        return self.rref.version
    def start_connecting(self, trigger_cb):
        raise NotImplementedError


@implementer(IStorageBroker)
class NoNetworkStorageBroker(object):  # type: ignore # missing many methods
    def get_servers_for_psi(self, peer_selection_index, for_upload=True):
        def _permuted(server):
            seed = server.get_permutation_seed()
            return permute_server_hash(peer_selection_index, seed)
        return sorted(self.get_connected_servers(), key=_permuted)
    def get_connected_servers(self):
        return self.client._servers
    def get_nickname_for_serverid(self, serverid):
        return None
    def when_connected_enough(self, threshold):
        return defer.Deferred()
    def get_all_serverids(self):
        return []  # FIXME?
    def get_known_servers(self):
        return []  # FIXME?


def create_no_network_client(basedir):
    """
    :return: a Deferred yielding an instance of _Client subclass which
        does no actual networking but has the same API.
    """
    basedir = abspath_expanduser_unicode(str(basedir))
    fileutil.make_dirs(os.path.join(basedir, "private"), 0o700)

    from allmydata.client import read_config
    config = read_config(basedir, u'client.port')
    storage_broker = NoNetworkStorageBroker()
    client = _NoNetworkClient(
        config,
        main_tub=None,
        i2p_provider=None,
        tor_provider=None,
        introducer_clients=[],
        storage_farm_broker=storage_broker
    )
    # this is a (pre-existing) reference-cycle and also a bad idea, see:
    # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2949
    storage_broker.client = client
    return defer.succeed(client)


class _NoNetworkClient(_Client):  # type: ignore  # tahoe-lafs/ticket/3573
    """
    Overrides all _Client networking functionality to do nothing.
    """

    def init_connections(self):
        pass
    def create_main_tub(self):
        pass
    def init_introducer_client(self):
        pass
    def create_log_tub(self):
        pass
    def setup_logging(self):
        pass
    def startService(self):
        service.MultiService.startService(self)
    def stopService(self):
        return service.MultiService.stopService(self)
    def init_helper(self):
        pass
    def init_key_gen(self):
        pass
    def init_storage(self):
        pass
    def init_client_storage_broker(self):
        self.storage_broker = NoNetworkStorageBroker()
        self.storage_broker.client = self
    def init_stub_client(self):
        pass
    #._servers will be set by the NoNetworkGrid which creates us


class SimpleStats(object):
    def __init__(self):
        self.counters = {}
        self.stats_producers = []

    def count(self, name, delta=1):
        val = self.counters.setdefault(name, 0)
        self.counters[name] = val + delta

    def register_producer(self, stats_producer):
        self.stats_producers.append(stats_producer)

    def get_stats(self):
        stats = {}
        for sp in self.stats_producers:
            stats.update(sp.get_stats())
        ret = { 'counters': self.counters, 'stats': stats }
        return ret

class NoNetworkGrid(service.MultiService):
    def __init__(self, basedir, num_clients, num_servers,
                 client_config_hooks, port_assigner):
        service.MultiService.__init__(self)

        # We really need to get rid of this pattern here (and
        # everywhere) in Tahoe where "async work" is started in
        # __init__ For now, we at least keep the errors so they can
        # cause tests to fail less-improperly (see _check_clients)
        self._setup_errors = []

        self.port_assigner = port_assigner
        self.basedir = basedir
        fileutil.make_dirs(basedir)

        self.servers_by_number = {} # maps to StorageServer instance
        self.wrappers_by_id = {} # maps to wrapped StorageServer instance
        self.proxies_by_id = {} # maps to IServer on which .rref is a wrapped
                                # StorageServer
        self.clients = []
        self.client_config_hooks = client_config_hooks

        for i in range(num_servers):
            ss = self.make_server(i)
            self.add_server(i, ss)
        self.rebuild_serverlist()

        for i in range(num_clients):
            d = self.make_client(i)
            d.addCallback(lambda c: self.clients.append(c))

            def _bad(f):
                self._setup_errors.append(f)
            d.addErrback(_bad)

    def _check_clients(self):
        """
        The anti-pattern of doing async work in __init__ means we need to
        check if that work completed successfully. This method either
        returns nothing or raises an exception in case __init__ failed
        to complete properly
        """
        if self._setup_errors:
            self._setup_errors[0].raiseException()

    @defer.inlineCallbacks
    def make_client(self, i, write_config=True):
        clientid = hashutil.tagged_hash(b"clientid", b"%d" % i)[:20]
        clientdir = os.path.join(self.basedir, "clients",
                                 idlib.shortnodeid_b2a(clientid))
        fileutil.make_dirs(clientdir)

        tahoe_cfg_path = os.path.join(clientdir, "tahoe.cfg")
        if write_config:
            from twisted.internet import reactor
            _, port_endpoint = self.port_assigner.assign(reactor)
            with open(tahoe_cfg_path, "w") as f:
                f.write("[node]\n")
                f.write("nickname = client-%d\n" % i)
                f.write("web.port = {}\n".format(port_endpoint))
                f.write("[storage]\n")
                f.write("enabled = false\n")
        else:
            _assert(os.path.exists(tahoe_cfg_path), tahoe_cfg_path=tahoe_cfg_path)

        c = None
        if i in self.client_config_hooks:
            # this hook can either modify tahoe.cfg, or return an
            # entirely new Client instance
            c = self.client_config_hooks[i](clientdir)

        if not c:
            c = yield create_no_network_client(clientdir)

        c.nodeid = clientid
        c.short_nodeid = b32encode(clientid).lower()[:8]
        c._servers = self.all_servers # can be updated later
        c.setServiceParent(self)
        defer.returnValue(c)

    def make_server(self, i, readonly=False):
        serverid = hashutil.tagged_hash(b"serverid", b"%d" % i)[:20]
        serverdir = os.path.join(self.basedir, "servers",
                                 idlib.shortnodeid_b2a(serverid), "storage")
        fileutil.make_dirs(serverdir)
        ss = StorageServer(serverdir, serverid, stats_provider=SimpleStats(),
                           readonly_storage=readonly)
        ss._no_network_server_number = i
        return ss

    def add_server(self, i, ss):
        # to deal with the fact that all StorageServers are named 'storage',
        # we interpose a middleman
        middleman = service.MultiService()
        middleman.setServiceParent(self)
        ss.setServiceParent(middleman)
        serverid = ss.my_nodeid
        self.servers_by_number[i] = ss
        wrapper = wrap_storage_server(FoolscapStorageServer(ss))
        self.wrappers_by_id[serverid] = wrapper
        self.proxies_by_id[serverid] = NoNetworkServer(serverid, wrapper)
        self.rebuild_serverlist()

    def get_all_serverids(self):
        return list(self.proxies_by_id.keys())

    def rebuild_serverlist(self):
        self._check_clients()
        self.all_servers = frozenset(list(self.proxies_by_id.values()))
        for c in self.clients:
            c._servers = self.all_servers

    def remove_server(self, serverid):
        # it's enough to remove the server from c._servers (we don't actually
        # have to detach and stopService it)
        for i,ss in list(self.servers_by_number.items()):
            if ss.my_nodeid == serverid:
                del self.servers_by_number[i]
                break
        del self.wrappers_by_id[serverid]
        del self.proxies_by_id[serverid]
        self.rebuild_serverlist()
        return ss

    def break_server(self, serverid, count=True):
        # mark the given server as broken, so it will throw exceptions when
        # asked to hold a share or serve a share. If count= is a number,
        # throw that many exceptions before starting to work again.
        self.wrappers_by_id[serverid].broken = count

    def hang_server(self, serverid):
        # hang the given server
        ss = self.wrappers_by_id[serverid]
        assert ss.hung_until is None
        ss.hung_until = defer.Deferred()

    def unhang_server(self, serverid):
        # unhang the given server
        ss = self.wrappers_by_id[serverid]
        assert ss.hung_until is not None
        ss.hung_until.callback(None)
        ss.hung_until = None

    def nuke_from_orbit(self):
        """ Empty all share directories in this grid. It's the only way to be sure ;-) """
        for server in list(self.servers_by_number.values()):
            for prefixdir in os.listdir(server.sharedir):
                if prefixdir != 'incoming':
                    fileutil.rm_dir(os.path.join(server.sharedir, prefixdir))


class GridTestMixin(object):
    def setUp(self):
        self.s = service.MultiService()
        self.s.startService()
        return super(GridTestMixin, self).setUp()

    def tearDown(self):
        return defer.gatherResults([
            self.s.stopService(),
            defer.maybeDeferred(super(GridTestMixin, self).tearDown),
        ])

    def set_up_grid(self, num_clients=1, num_servers=10,
                    client_config_hooks=None, oneshare=False):
        """
        Create a Tahoe-LAFS storage grid.

        :param num_clients: See ``NoNetworkGrid``
        :param num_servers: See `NoNetworkGrid``
        :param client_config_hooks: See ``NoNetworkGrid``

        :param bool oneshare: If ``True`` then the first client node is
            configured with ``n == k == happy == 1``.

        :return: ``None``
        """
        if client_config_hooks is None:
            client_config_hooks = {}
        # self.basedir must be set
        port_assigner = SameProcessStreamEndpointAssigner()
        port_assigner.setUp()
        self.addCleanup(port_assigner.tearDown)
        self.g = NoNetworkGrid(self.basedir,
                               num_clients=num_clients,
                               num_servers=num_servers,
                               client_config_hooks=client_config_hooks,
                               port_assigner=port_assigner,
        )
        self.g.setServiceParent(self.s)
        if oneshare:
            c = self.get_client(0)
            c.encoding_params["k"] = 1
            c.encoding_params["happy"] = 1
            c.encoding_params["n"] = 1
        self._record_webports_and_baseurls()

    def _record_webports_and_baseurls(self):
        self.g._check_clients()
        self.client_webports = [c.getServiceNamed("webish").getPortnum()
                                for c in self.g.clients]
        self.client_baseurls = [c.getServiceNamed("webish").getURL()
                                for c in self.g.clients]

    def get_client_config(self, i=0):
        self.g._check_clients()
        return self.g.clients[i].config

    def get_clientdir(self, i=0):
        # ideally, use something get_client_config() only, we
        # shouldn't need to manipulate raw paths..
        return self.get_client_config(i).get_config_path()

    def get_client(self, i=0):
        self.g._check_clients()
        return self.g.clients[i]

    def restart_client(self, i=0):
        self.g._check_clients()
        client = self.g.clients[i]
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.g.removeService(client))

        @defer.inlineCallbacks
        def _make_client(ign):
            c = yield self.g.make_client(i, write_config=False)
            self.g.clients[i] = c
            self._record_webports_and_baseurls()
        d.addCallback(_make_client)
        return d

    def get_serverdir(self, i):
        return self.g.servers_by_number[i].storedir

    def iterate_servers(self):
        for i in sorted(self.g.servers_by_number.keys()):
            ss = self.g.servers_by_number[i]
            yield (i, ss, ss.storedir)

    def find_uri_shares(self, uri):
        si = tahoe_uri.from_string(uri).get_storage_index()
        prefixdir = storage_index_to_dir(si)
        shares = []
        for i,ss in list(self.g.servers_by_number.items()):
            serverid = ss.my_nodeid
            basedir = os.path.join(ss.sharedir, prefixdir)
            if not os.path.exists(basedir):
                continue
            for f in os.listdir(basedir):
                try:
                    shnum = int(f)
                    shares.append((shnum, serverid, os.path.join(basedir, f)))
                except ValueError:
                    pass
        return sorted(shares)

    def copy_shares(self, uri: bytes) -> dict[bytes, bytes]:
        """
        Read all of the share files for the given capability from the storage area
        of the storage servers created by ``set_up_grid``.

        :param bytes uri: A Tahoe-LAFS data capability.

        :return: A ``dict`` mapping share file names to share file contents.
        """
        shares = {}
        for (shnum, serverid, sharefile) in self.find_uri_shares(uri):
            with open(sharefile, "rb") as f:
                shares[sharefile] = f.read()
        return shares

    def restore_all_shares(self, shares):
        for sharefile, data in list(shares.items()):
            with open(sharefile, "wb") as f:
                f.write(data)

    def delete_share(self, sharenum_and_serverid_and_sharefile):
        (shnum, serverid, sharefile) = sharenum_and_serverid_and_sharefile
        os.unlink(sharefile)

    def delete_shares_numbered(self, uri, shnums):
        for (i_shnum, i_serverid, i_sharefile) in self.find_uri_shares(uri):
            if i_shnum in shnums:
                os.unlink(i_sharefile)

    def delete_all_shares(self, serverdir):
        sharedir = os.path.join(serverdir, "shares")
        for prefixdir in os.listdir(sharedir):
            if prefixdir != 'incoming':
                fileutil.rm_dir(os.path.join(sharedir, prefixdir))

    def corrupt_share(self, sharenum_and_serverid_and_sharefile, corruptor_function):
        (shnum, serverid, sharefile) = sharenum_and_serverid_and_sharefile
        with open(sharefile, "rb") as f:
            sharedata = f.read()
        corruptdata = corruptor_function(sharedata)
        with open(sharefile, "wb") as f:
            f.write(corruptdata)

    def corrupt_shares_numbered(self, uri, shnums, corruptor, debug=False):
        for (i_shnum, i_serverid, i_sharefile) in self.find_uri_shares(uri):
            if i_shnum in shnums:
                with open(i_sharefile, "rb") as f:
                    sharedata = f.read()
                corruptdata = corruptor(sharedata, debug=debug)
                with open(i_sharefile, "wb") as f:
                    f.write(corruptdata)

    def corrupt_all_shares(self, uri: bytes, corruptor: Callable[[bytes, bool], bytes], debug: bool=False):
        """
        Apply ``corruptor`` to the contents of all share files associated with a
        given capability and replace the share file contents with its result.
        """
        for (i_shnum, i_serverid, i_sharefile) in self.find_uri_shares(uri):
            with open(i_sharefile, "rb") as f:
                sharedata = f.read()
            corruptdata = corruptor(sharedata, debug)
            with open(i_sharefile, "wb") as f:
                f.write(corruptdata)

    @defer.inlineCallbacks
    def GET(self, urlpath, followRedirect=False, return_response=False,
            method="GET", clientnum=0, **kwargs):
        # if return_response=True, this fires with (data, statuscode,
        # respheaders) instead of just data.
        url = self.client_baseurls[clientnum] + ensure_text(urlpath)

        response = yield treq.request(method, url, persistent=False,
                                      allow_redirects=followRedirect,
                                      **kwargs)
        data = yield response.content()
        if return_response:
            # we emulate the old HTTPClientGetFactory-based response, which
            # wanted a tuple of (bytestring of data, bytestring of response
            # code like "200" or "404", and a
            # twisted.web.http_headers.Headers instance). Fortunately treq's
            # response.headers has one.
            defer.returnValue( (data, str(response.code), response.headers) )
        if 400 <= response.code < 600:
            raise Error(response.code, response=data)
        defer.returnValue(data)

    def PUT(self, urlpath, **kwargs):
        return self.GET(urlpath, method="PUT", **kwargs)
