# -*- python -*-

from allmydata import client
from twisted.application import service

queen_pburl = "pb://jekyv6ghn7zinppk7wcvfmk7o4gw76hb@192.168.1.101:42552/roster"
yumyum_queen = "pb://cznyjh2pi4bybn3g7pi36bdfnwz356vk@192.168.1.98:56510/roster"
c = client.Client()
c.set_queen_pburl(yumyum_queen)
#c.set_queen_pburl(queen_pburl)

application = service.Application("allmydata_client")
c.setServiceParent(application)
