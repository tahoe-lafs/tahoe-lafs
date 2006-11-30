
from foolscap import Tub
from twisted.application import service

class Client(service.MultiService):
    def __init__(self, queen_pburl):
        service.MultiService.__init__(self)
        self.queen_pburl = queen_pburl
        self.tub = Tub()
