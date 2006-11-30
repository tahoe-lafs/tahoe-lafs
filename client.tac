# -*- python -*-

from allmydata import client
from twisted.application import service

queen_host = "yumyum"
queen_pburl = "pb://jekyv6ghn7zinppk7wcvfmk7o4gw76hb@192.168.1.101:42552/roster"
c = client.Client(queen_host, queen_pburl)

application = service.Application("allmydata_client")
c.setServiceParent(application)
