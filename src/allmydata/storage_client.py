
"""
I contain the client-side code which speaks to storage servers, in particular
the foolscap-based server implemented in src/allmydata/storage/*.py .
"""

# roadmap:
#
#  implement ServerFarm, change Client to create it, change
#  uploader/servermap to get rrefs from it. ServerFarm calls
#  IntroducerClient.subscribe_to .
#
#  implement NativeStorageClient, change Tahoe2PeerSelector to use it. All
#  NativeStorageClients come from the introducer
#
#  change web/check_results.py to get NativeStorageClients from check results,
#  ask it for a nickname (instead of using client.get_nickname_for_serverid)
#
#  implement tahoe.cfg scanner, create static NativeStorageClients

import sha
from zope.interface import implements
from allmydata.interfaces import IStorageBroker

class StorageFarmBroker:
    implements(IStorageBroker)
    """I live on the client, and know about storage servers. For each server
    that is participating in a grid, I either maintain a connection to it or
    remember enough information to establish a connection to it on demand.
    I'm also responsible for subscribing to the IntroducerClient to find out
    about new servers as they are announced by the Introducer.
    """
    def __init__(self, permute_peers=True):
        assert permute_peers # False not implemented yet
        self.servers = {} # serverid -> StorageClient instance
        self.permute_peers = permute_peers
        self.introducer_client = None
    def add_server(self, serverid, s):
        self.servers[serverid] = s
    def use_introducer(self, introducer_client):
        self.introducer_client = ic = introducer_client
        ic.subscribe_to("storage")

    def get_servers_for_index(self, peer_selection_index):
        # first cut: return a list of (peerid, versioned-rref) tuples
        assert self.permute_peers == True
        servers = self.get_all_servers()
        key = peer_selection_index
        return sorted(servers, key=lambda x: sha.new(key+x[0]).digest())

    def get_all_servers(self):
        # return a frozenset of (peerid, versioned-rref) tuples
        servers = {}
        for serverid,server in self.servers.items():
            servers[serverid] = server
        if self.introducer_client:
            ic = self.introducer_client
            for serverid,server in ic.get_peers("storage"):
                servers[serverid] = server
        return frozenset(servers.items())

    def get_all_serverids(self):
        for serverid in self.servers:
            yield serverid
        if self.introducer_client:
            for serverid,server in self.introducer_client.get_peers("storage"):
                yield serverid

    def get_nickname_for_serverid(self, serverid):
        if serverid in self.servers:
            return self.servers[serverid].nickname
        if self.introducer_client:
            return self.introducer_client.get_nickname_for_peerid(serverid)
        return None

class NativeStorageClient:
    def __init__(self, serverid, furl, nickname, min_shares=1):
        self.serverid = serverid
        self.furl = furl
        self.nickname = nickname
        self.min_shares = min_shares

class UnknownServerTypeError(Exception):
    pass
