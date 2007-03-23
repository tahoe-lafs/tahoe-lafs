class IntroducerClient(Referenceable):
    implements(RIIntroducerClient)

    def __init__(self, tub, introducer_pburl, my_pburl):
        self.introducer_reconnector = self.tub.connectTo(introducer_pburl,
                                                  self._got_introducer)

        self.tub = tub
        self.my_pburl = my_pburl

        self.connections = {} # k: nodeid, v: ref
        self.reconnectors = {} # k: PBURL, v: reconnector

    def remote_get_nodeid(self):
        return self.nodeid

    def remote_new_peers(self, pburls):
        for pburl in pburls:
            self._new_peer(pburl)

    def stop(self):
        self.introducer_reconnector.stopConnecting()
        for reconnector in self.reconnectors.itervalues():
            reconnector.stopConnecting()

    def _new_peer(self, pburl):
        if pburl in self.reconnectors:
            return
        def _got_peer(rref):
            d2 = rref.callRemote("get_nodeid")
            def _got_nodeid(nodeid):
                self.connections[nodeid] = rref
                def _lost():
                    # TODO: notifyOnDisconnect uses eventually(), but connects do not. Could this cause a problem?
                    del self.connections[nodeid]
                rref.notifyOnDisconnect(_lost)
            d2.addCallback(_got_nodeid)
        log.msg(" connecting to(%s)" % pburl)
        self.reconnectors[pburl] = self.tub.connectTo(pburl, _got_peer)

    def _got_introducer(self, introducer):
        log.msg(" introducing ourselves: %s, %s" % (self, self.my_pburl))
        d = introducer.callRemote("hello",
                             node=self,
                             pburl=self.my_pburl)
