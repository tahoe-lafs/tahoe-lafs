
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

import os.path
import sha
from twisted.application import service
from foolscap import Referenceable
from foolscap.eventual import fireEventually
from base64 import b32encode
from allmydata.client import Client
from allmydata.storage import StorageServer
from allmydata.util import fileutil, idlib, hashutil, rrefutil
from allmydata.introducer.client import RemoteServiceConnector

class IntentionalError(Exception):
    pass

class Marker:
    pass

class LocalWrapper:
    def __init__(self, original):
        self.original = original
        self.broken = False
        self.post_call_notifier = None
        self.disconnectors = {}

    def callRemoteOnly(self, methname, *args, **kwargs):
        d = self.callRemote(methname, *args, **kwargs)
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
        def _call():
            if self.broken:
                raise IntentionalError("I was asked to break")
            meth = getattr(self.original, "remote_" + methname)
            return meth(*args, **kwargs)
        d = fireEventually()
        d.addCallback(lambda res: _call())
        def _return_membrane(res):
            # rather than complete the difficult task of building a
            # fully-general Membrane (which would locate all Referenceable
            # objects that cross the simulated wire and replace them with
            # wrappers), we special-case certain methods that we happen to
            # know will return Referenceables.
            if methname == "allocate_buckets":
                (alreadygot, allocated) = res
                for shnum in allocated:
                    allocated[shnum] = LocalWrapper(allocated[shnum])
            if methname == "get_buckets":
                for shnum in res:
                    res[shnum] = LocalWrapper(res[shnum])
            return res
        d.addCallback(_return_membrane)
        if self.post_call_notifier:
            d.addCallback(self.post_call_notifier, methname)
        return d

    def notifyOnDisconnect(self, f, *args, **kwargs):
        m = Marker()
        self.disconnectors[m] = (f, args, kwargs)
        return m
    def dontNotifyOnDisconnect(self, marker):
        del self.disconnectors[marker]

def wrap(original, service_name):
    # The code in immutable.checker insists upon asserting the truth of
    # isinstance(rref, rrefutil.WrappedRemoteReference). Much of the
    # upload/download code uses rref.version (which normally comes from
    # rrefutil.VersionedRemoteReference). To avoid using a network, we want a
    # LocalWrapper here. Try to satisfy all these constraints at the same
    # time.
    local = LocalWrapper(original)
    wrapped = rrefutil.WrappedRemoteReference(local)
    try:
        version = original.remote_get_version()
    except AttributeError:
        version = RemoteServiceConnector.VERSION_DEFAULTS[service_name]
    wrapped.version = version
    return wrapped

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
        raise RuntimeError("NoNetworkClient has no Tub")
    def init_control(self):
        pass
    def init_helper(self):
        pass
    def init_key_gen(self):
        pass
    def init_storage(self):
        pass
    def init_stub_client(self):
        pass

    def get_servers(self, service_name):
        return self._servers

    def get_permuted_peers(self, service_name, key):
        return sorted(self._servers, key=lambda x: sha.new(key+x[0]).digest())


class NoNetworkGrid(service.MultiService):
    def __init__(self, basedir, num_clients=1, num_servers=10,
                 client_config_hooks={}):
        service.MultiService.__init__(self)
        self.basedir = basedir
        fileutil.make_dirs(basedir)

        self.servers_by_number = {}
        self.servers_by_id = {}
        self.clients = []

        for i in range(num_servers):
            serverid = hashutil.tagged_hash("serverid", str(i))[:20]
            serverdir = os.path.join(basedir, "servers",
                                     idlib.shortnodeid_b2a(serverid))
            fileutil.make_dirs(serverdir)
            ss = StorageServer(serverdir)
            self.add_server(i, serverid, ss)

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
            c.nodeid = clientid
            c.short_nodeid = b32encode(clientid).lower()[:8]
            c._servers = self.all_servers # can be updated later
            c.setServiceParent(self)
            self.clients.append(c)

    def add_server(self, i, serverid, ss):
        # TODO: ss.setServiceParent(self), but first remove the goofy
        # self.parent.nodeid from Storage.startService . At the moment,
        # Storage doesn't really need to be startService'd, but it will in
        # the future.
        ss.setNodeID(serverid)
        self.servers_by_number[i] = ss
        self.servers_by_id[serverid] = wrap(ss, "storage")
        self.all_servers = frozenset(self.servers_by_id.items())
        for c in self.clients:
            c._servers = self.all_servers

class GridTestMixin:
    def setUp(self):
        self.s = service.MultiService()
        self.s.startService()

    def tearDown(self):
        return self.s.stopService()

    def set_up_grid(self, client_config_hooks={}):
        # self.basedir must be set
        self.g = NoNetworkGrid(self.basedir,
                               client_config_hooks=client_config_hooks)
        self.g.setServiceParent(self.s)

    def get_clientdir(self, i=0):
        return self.g.clients[i].basedir

    def get_serverdir(self, i):
        return self.g.servers_by_number[i].storedir

    def iterate_servers(self):
        for i in sorted(self.g.servers_by_number.keys()):
            ss = self.g.servers_by_number[i]
            yield (i, ss, ss.storedir)
