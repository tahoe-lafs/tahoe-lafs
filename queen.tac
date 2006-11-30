# -*- python -*-

from allmydata import queen
from twisted.application import service

c = queen.Queen()

application = service.Application("allmydata_queen")
c.setServiceParent(application)
