
from twisted.internet import defer
from nevow import rend, inevow, tags as T
from allmydata.web.common import getxmlfile
from allmydata.interfaces import IntroducerlessConfigDisabledError

class IntroducerlessConfig(rend.Page):

    docFactory = getxmlfile("introducerless_config.xhtml")

    def __init__(self, client):
        self.client = client

    def data_servers(self, ctx, data):
        if not self.client.get_config("node", "web.reveal_storage_furls",
                                                   default=False, boolean=True):
            raise IntroducerlessConfigDisabledError()

        sb = self.client.get_storage_broker()
        return sorted(sb.get_known_servers(), key=lambda s: s.get_serverid())

    def render_server_config(self, ctx, server):

        announcement = server.get_announcement()
        seed = announcement['permutation-seed-base32']
        furl = announcement['anonymous-storage-FURL']
        peerid = server.get_longname()
        nickname = server.get_nickname()

        config = """
server.%s.type = tahoe-foolscap
server.%s.nickname = %s
server.%s.seed = %s
server.%s.furl = %s
""" % (peerid, peerid, nickname, peerid, seed, peerid, furl)

        return config
        
