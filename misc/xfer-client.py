#! /usr/bin/python

"""This program is a client that sends files to xfer-server.py. You give it
the server's FURL, and it can put files into the server's targetdir (and
nowhere else). When you want an unattended process on one machine to be able
to place files in a remote directory, you could give its parent process an
ssh account on the target, with an empty passphrase, but that provides too
much power. This program is a least-privilege replacement for the ssh/scp
approach.

Give the client a FURL, or a file where the FURL is stored. You also give it
the name of the local file to be transferred. The last component of the local
pathname will be used as the remote filename.
"""

import os.path
from twisted.internet import reactor
from foolscap import UnauthenticatedTub
from twisted.python import usage

class Options(usage.Options):
    synopsis = "xfer-client.py (--furl FURL | --furlfile furlfile) LOCALFILE"
    optParameters = [
        ["furl", "f", None,
         "The server FURL. You must either provide --furl or --furlfile."],
        ["furlfile", "l", None,
         "A file containing the server FURL."],
        ]
    optFlags = [
        ["quiet", "q", "Do not announce success."],
        ]

    def parseArgs(self, localfile):
        self['localfile'] = localfile

    def postOptions(self):
        if not self["furl"] and not self["furlfile"]:
            raise usage.UsageError("you must either provide --furl or --furlfile")
        if not os.path.exists(self["localfile"]):
            raise usage.UsageError("local file '%s' doesn't exist" % self["localfile"])

opts = Options()
opts.parseOptions()
tub = UnauthenticatedTub()
tub.startService()
if opts["furl"]:
    furl = opts["furl"]
else:
    furl = open(os.path.expanduser(opts["furlfile"]), "r").read().strip()
remotename = os.path.basename(opts["localfile"])
d = tub.getReference(furl)
def _push(rref):
    data = open(os.path.expanduser(opts["localfile"]), "r").read()
    return rref.callRemote("putfile", remotename, data)
d.addCallback(_push)
def _success(res):
    reactor.stop()
    if not opts["quiet"]:
        print "file transferred to %s" % remotename
def _failure(f):
    reactor.stop()
    print "error while transferring file:"
    print f
d.addCallbacks(_success, _failure)

reactor.run()
