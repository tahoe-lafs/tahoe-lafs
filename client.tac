# -*- python -*-

from allmydata import client
from twisted.application import service

queen_host = "yumyum"
queen_pburl = ""
c = client.Client(queen_host, queen_pburl)

application = service.Application("allmydata_client")
c.setServiceParent(application)
