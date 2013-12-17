#!/usr/bin/env python
# -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-

# this depends upon Twisted and Nevow, but not upon Tahoe itself

import webbrowser

from twisted.application import strports
from twisted.internet import reactor
from nevow import appserver, rend, loaders
from twisted.web import static
import web_reliability, provisioning

class Root(rend.Page):
    docFactory = loaders.xmlstr('''\
<html xmlns:n="http://nevow.com/ns/nevow/0.1">
  <head>
    <title>Tahoe-LAFS Provisioning/Reliability Calculator</title>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
  </head>
  <body>
  <p><a href="reliability">Reliability Tool</a></p>
  <p><a href="provisioning">Provisioning Tool</a></p>
  </body>
</html>
''')

    child_reliability = web_reliability.ReliabilityTool()
    child_provisioning = provisioning.ProvisioningTool()


def run(portnum):
    root = Root()
    root.putChild("tahoe.css", static.File("tahoe.css"))
    site = appserver.NevowSite(root)
    s = strports.service("tcp:%d" % portnum, site)
    s.startService()
    reactor.callLater(1.0, webbrowser.open, "http://localhost:%d/" % portnum)
    reactor.run()

if __name__ == '__main__':
    import sys
    portnum = 8070
    if len(sys.argv) > 1:
        portnum = int(sys.argv[1])
    run(portnum)
