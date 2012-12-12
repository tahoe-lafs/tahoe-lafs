
# This contains a test harness that creates a full Tahoe grid in a single
# process (actually in a single MultiService) which does not use the network.
# It does not use an Introducer, and there are no foolscap Tubs. Each storage
# server puts real shares on disk, but is accessed through loopback
# RemoteReferences instead of over serialized SSL. It is not as complete as
# the common.SystemTestMixin framework (which does use the network), but
# should be considerably faster: on my laptop, it takes 50-80ms to start up,
# whereas SystemTestMixin takes close to 2s.

# This should be useful for tests which want to examine and/or manipulate the
# uploaded shares, checker/verifier/repairer tests, etc. The clients have no
# Tubs, so it is not useful for tests that involve a Helper, a KeyGenerator,
# or the control.furl .

import os.path, shutil

from zope.interface import implements
from twisted.application import service
from twisted.internet import defer, reactor
from twisted.python.failure import Failure
from foolscap.api import Referenceable, fireEventually, RemoteException
from base64 import b32encode

from allmydata import uri as tahoe_uri
from allmydata.client import Client
from allmydata.storage.server import StorageServer
from allmydata.storage.backends.disk.disk_backend import DiskBackend
from allmydata.util import fileutil, idlib, hashutil, log
from allmydata.util.hashutil import sha1
from allmydata.test.common_web import HTTPClientGETFactory
from allmydata.interfaces import IStorageBroker, IServer
from allmydata.test.common import TEST_RSA_KEY_SIZE


PRINT_TRACEBACKS = False

class IntentionalError(Exception):
    pass

class Marker:
    pass

class LocalWrapper:
    def __init__(self, original):
        self.original = original
        self.broken = False
        self.hung_until = None
        self.post_call_notifier = None
        self.disconnectors = {}
        self.counter_by_methname = {}

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
                return LocalWrapper(a)
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

        if PRINT_TRACEBACKS:
            import traceback
            tb = traceback.extract_stack()
        d = fireEventually()
        d.addCallback(lambda res: _call())
        def _wrap_exception(f):
            if PRINT_TRACEBACKS and not f.check(NameError):
                print ">>>" + ">>>".join(traceback.format_list(tb))
                print "+++ %s%r %r: %s" % (methname, args, kwargs, f)
                #f.printDetailedTraceback()
            return Failure(RemoteException(f))
        d.addErrback(_wrap_exception)
        def _return_membrane(res):
            # Rather than complete the difficult task of building a
            # fully-general Membrane (which would locate all Referenceable
            # objects that cross the simulated wire and replace them with
            # wrappers), we special-case certain methods that we happen to
            # know will return Referenceables.
            # The outer return value of such a method may be Deferred, but
            # its components must not be.
            if methname == "allocate_buckets":
                (alreadygot, allocated) = res
                for shnum in allocated:
                    assert not isinstance(allocated[shnum], defer.Deferred), (methname, allocated)
                    allocated[shnum] = LocalWrapper(allocated[shnum])
            if methname == "get_buckets":
                for shnum in res:
                    assert not isinstance(res[shnum], defer.Deferred), (methname, res)
                    res[shnum] = LocalWrapper(res[shnum])
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

def wrap_storage_server(original):
    # Much of the upload/download code uses rref.version (which normally
    # comes from rrefutil.add_version_to_remote_reference). To avoid using a
    # network, we want a LocalWrapper here. Try to satisfy all these
    # constraints at the same time.
    wrapper = LocalWrapper(original)
    wrapper.version = original.remote_get_version()
    return wrapper

class NoNetworkServer:
    implements(IServer)
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
    def get_serverid(self):
        return self.serverid
    def get_permutation_seed(self):
        return self.serverid
    def get_lease_seed(self):
        return self.serverid
    def get_foolscap_write_enabler_seed(self):
        return self.serverid

    def get_name(self):
        return idlib.shortnodeid_b2a(self.serverid)
    def get_longname(self):
        return idlib.nodeid_b2a(self.serverid)
    def get_nickname(self):
        return "nickname"
    def get_rref(self):
        return self.rref
    def get_version(self):
        return self.rref.version

class NoNetworkStorageBroker:
    implements(IStorageBroker)
    def get_servers_for_psi(self, peer_selection_index):
        def _permuted(server):
            seed = server.get_permutation_seed()
            return sha1(peer_selection_index + seed).digest()
        return sorted(self.get_connected_servers(), key=_permuted)

    def get_connected_servers(self):
        return self.client._servers

    def get_nickname_for_serverid(self, serverid):
        return None

    def get_known_servers(self):
        return self.get_connected_servers()

    def get_all_serverids(self):
        return self.client.get_all_serverids()


class NoNetworkClient(Client):
    def create_tub(self):
        pass
    def init_introducer_client(self):
        pass
    def setup_logging(self):
        pass
    def startService(self):
        service.MultiService.startService(self)
    def stopService(self):
        service.MultiService.stopService(self)
    def when_tub_ready(self):
        raise NotImplementedError("NoNetworkClient has no Tub")
    def init_control(self):
        pass
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

class SimpleStats:
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
    def __init__(self, basedir, num_clients=1, num_servers=10,
                 client_config_hooks={}):
        service.MultiService.__init__(self)
        self.basedir = basedir
        fileutil.make_dirs(basedir)

        self.servers_by_number = {} # maps to StorageServer instance
        self.wrappers_by_id = {} # maps to wrapped StorageServer instance
        self.proxies_by_id = {} # maps to IServer on which .rref is a wrapped
                                # StorageServer
        self.clients = []

        for i in range(num_servers):
            server = self.make_server(i)
            self.add_server(i, server)
        self.rebuild_serverlist()

        for i in range(num_clients):
            clientid = hashutil.tagged_hash("clientid", str(i))[:20]
            clientdir = os.path.join(basedir, "clients",
                                     idlib.shortnodeid_b2a(clientid))
            fileutil.make_dirs(clientdir)
            f = open(os.path.join(clientdir, "tahoe.cfg"), "w")
            f.write("[node]\n")
            f.write("nickname = client-%d\n" % i)
            f.write("web.port = tcp:0:interface=127.0.0.1\n")
            f.write("[storage]\n")
            f.write("enabled = false\n")
            f.close()
            c = None
            if i in client_config_hooks:
                # this hook can either modify tahoe.cfg, or return an
                # entirely new Client instance
                c = client_config_hooks[i](clientdir)
            if not c:
                c = NoNetworkClient(clientdir)
                c.set_default_mutable_keysize(TEST_RSA_KEY_SIZE)
            c.nodeid = clientid
            c.short_nodeid = b32encode(clientid).lower()[:8]
            c._servers = self.all_servers # can be updated later
            c.setServiceParent(self)
            self.clients.append(c)

    def make_server(self, i, readonly=False):
        serverid = hashutil.tagged_hash("serverid", str(i))[:20]
        storagedir = os.path.join(self.basedir, "servers",
                                  idlib.shortnodeid_b2a(serverid), "storage")

        # The backend will make the storage directory and any necessary parents.
        backend = DiskBackend(storagedir, readonly=readonly)
        server = StorageServer(serverid, backend, storagedir, stats_provider=SimpleStats())
        server._no_network_server_number = i
        return server

    def add_server(self, i, server):
        # to deal with the fact that all StorageServers are named 'storage',
        # we interpose a middleman
        middleman = service.MultiService()
        middleman.setServiceParent(self)
        server.setServiceParent(middleman)
        serverid = server.get_serverid()
        self.servers_by_number[i] = server
        aa = server.get_accountant().get_anonymous_account()
        wrapper = wrap_storage_server(aa)
        self.wrappers_by_id[serverid] = wrapper
        self.proxies_by_id[serverid] = NoNetworkServer(serverid, wrapper)
        self.rebuild_serverlist()

    def get_all_serverids(self):
        return self.proxies_by_id.keys()

    def rebuild_serverlist(self):
        self.all_servers = frozenset(self.proxies_by_id.values())
        for c in self.clients:
            c._servers = self.all_servers

    def remove_server(self, serverid):
        # it's enough to remove the server from c._servers (we don't actually
        # have to detach and stopService it)
        for i, server in self.servers_by_number.items():
            if server.get_serverid() == serverid:
                del self.servers_by_number[i]
                break
        del self.wrappers_by_id[serverid]
        del self.proxies_by_id[serverid]
        self.rebuild_serverlist()
        return server

    def break_server(self, serverid, count=True):
        # mark the given server as broken, so it will throw exceptions when
        # asked to hold a share or serve a share. If count= is a number,
        # throw that many exceptions before starting to work again.
        self.wrappers_by_id[serverid].broken = count

    def hang_server(self, serverid):
        # hang the given server
        server = self.wrappers_by_id[serverid]
        assert server.hung_until is None
        server.hung_until = defer.Deferred()

    def unhang_server(self, serverid):
        # unhang the given server
        server = self.wrappers_by_id[serverid]
        assert server.hung_until is not None
        server.hung_until.callback(None)
        server.hung_until = None

    def nuke_from_orbit(self):
        """Empty all share directories in this grid. It's the only way to be sure ;-)
        This works only for a disk backend."""
        for server in self.servers_by_number.values():
            sharedir = server.backend._sharedir
            for prefixdir in os.listdir(sharedir):
                if prefixdir != 'incoming':
                    fileutil.rm_dir(os.path.join(sharedir, prefixdir))


class GridTestMixin:
    def setUp(self):
        self.s = service.MultiService()
        self.s.startService()

    def tearDown(self):
        return self.s.stopService()

    def set_up_grid(self, num_clients=1, num_servers=10,
                    client_config_hooks={}):
        # self.basedir must be set
        self.g = NoNetworkGrid(self.basedir,
                               num_clients=num_clients,
                               num_servers=num_servers,
                               client_config_hooks=client_config_hooks)
        self.g.setServiceParent(self.s)
        self.client_webports = [c.getServiceNamed("webish").getPortnum()
                                for c in self.g.clients]
        self.client_baseurls = [c.getServiceNamed("webish").getURL()
                                for c in self.g.clients]

    def get_clientdir(self, i=0):
        return self.g.clients[i].basedir

    def get_server(self, i):
        return self.g.servers_by_number[i]

    def get_serverdir(self, i):
        return self.g.servers_by_number[i].backend._storedir

    def remove_server(self, i):
        self.g.remove_server(self.g.servers_by_number[i].get_serverid())

    def iterate_servers(self):
        for i in sorted(self.g.servers_by_number.keys()):
            server = self.g.servers_by_number[i]
            yield (i, server, server.backend._storedir)

    def find_uri_shares(self, uri):
        si = tahoe_uri.from_string(uri).get_storage_index()
        sharelist = []
        d = defer.succeed(None)
        for i, server in self.g.servers_by_number.items():
            d.addCallback(lambda ign, server=server: server.backend.get_shareset(si).get_shares())
            def _append_shares( (shares_for_server, corrupted), server=server):
                assert len(corrupted) == 0, (shares_for_server, corrupted)
                for share in shares_for_server:
                    assert not isinstance(share, defer.Deferred), share
                    sharelist.append( (share.get_shnum(), server.get_serverid(), share._get_path()) )
            d.addCallback(_append_shares)

        d.addCallback(lambda ign: sorted(sharelist))
        return d

    def add_server(self, server_number, readonly=False):
        assert self.g, "I tried to find a grid at self.g, but failed"
        ss = self.g.make_server(server_number, readonly)
        log.msg("just created a server, number: %s => %s" % (server_number, ss,))
        self.g.add_server(server_number, ss)

    def add_server_with_share(self, uri, server_number, share_number=None,
                              readonly=False):
        self.add_server(server_number, readonly)
        if share_number is not None:
            self.copy_share_to_server(uri, server_number, share_number)

    def copy_share_to_server(self, uri, server_number, share_number):
        ss = self.g.servers_by_number[server_number]
        self.copy_share(self.shares[share_number], uri, ss)

    def copy_shares(self, uri):
        shares = {}
        d = self.find_uri_shares(uri)
        def _got_shares(sharelist):
            for (shnum, serverid, sharefile) in sharelist:
                shares[sharefile] = fileutil.read(sharefile)

            return shares
        d.addCallback(_got_shares)
        return d

    def copy_share(self, from_share, uri, to_server):
        si = tahoe_uri.from_string(uri).get_storage_index()
        (i_shnum, i_serverid, i_sharefile) = from_share
        shares_dir = to_server.backend.get_shareset(si)._get_sharedir()
        new_sharefile = os.path.join(shares_dir, str(i_shnum))
        fileutil.make_dirs(shares_dir)
        if os.path.normpath(i_sharefile) != os.path.normpath(new_sharefile):
            shutil.copy(i_sharefile, new_sharefile)

    def restore_all_shares(self, shares):
        for sharefile, data in shares.items():
            fileutil.write(sharefile, data)

    def delete_share(self, (shnum, serverid, sharefile)):
        fileutil.remove(sharefile)

    def delete_shares_numbered(self, uri, shnums):
        d = self.find_uri_shares(uri)
        def _got_shares(sharelist):
            for (i_shnum, i_serverid, i_sharefile) in sharelist:
                if i_shnum in shnums:
                    fileutil.remove(i_sharefile)
        d.addCallback(_got_shares)
        return d

    def delete_all_shares(self, uri):
        d = self.find_uri_shares(uri)
        def _got_shares(shares):
            for sh in shares:
                self.delete_share(sh)
        d.addCallback(_got_shares)
        return d

    def corrupt_share(self, (shnum, serverid, sharefile), corruptor_function, debug=False):
        sharedata = fileutil.read(sharefile)
        corruptdata = corruptor_function(sharedata, debug=debug)
        fileutil.write(sharefile, corruptdata)

    def corrupt_shares_numbered(self, uri, shnums, corruptor, debug=False):
        d = self.find_uri_shares(uri)
        def _got_shares(sharelist):
            for (i_shnum, i_serverid, i_sharefile) in sharelist:
                if i_shnum in shnums:
                    self.corrupt_share((i_shnum, i_serverid, i_sharefile), corruptor, debug=debug)
        d.addCallback(_got_shares)
        return d

    def corrupt_all_shares(self, uri, corruptor, debug=False):
        d = self.find_uri_shares(uri)
        def _got_shares(sharelist):
            for (i_shnum, i_serverid, i_sharefile) in sharelist:
                self.corrupt_share((i_shnum, i_serverid, i_sharefile), corruptor, debug=debug)
        d.addCallback(_got_shares)
        return d

    def GET(self, urlpath, followRedirect=False, return_response=False,
            method="GET", clientnum=0, **kwargs):
        # if return_response=True, this fires with (data, statuscode,
        # respheaders) instead of just data.
        assert not isinstance(urlpath, unicode)
        url = self.client_baseurls[clientnum] + urlpath
        factory = HTTPClientGETFactory(url, method=method,
                                       followRedirect=followRedirect, **kwargs)
        reactor.connectTCP("localhost", self.client_webports[clientnum],factory)
        d = factory.deferred
        def _got_data(data):
            return (data, factory.status, factory.response_headers)
        if return_response:
            d.addCallback(_got_data)
        return factory.deferred

    def PUT(self, urlpath, **kwargs):
        return self.GET(urlpath, method="PUT", **kwargs)
