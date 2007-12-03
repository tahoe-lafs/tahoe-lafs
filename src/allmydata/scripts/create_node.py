
import os, sys
from twisted.python import usage
from allmydata.scripts.common import BasedirMixin, NoDefaultBasedirMixin

class CreateClientOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to create the client in"],
        ["webport", "p", "tcp:8123:interface=127.0.0.1",
         "which TCP port to run the HTTP interface on. Use 'none' to disable."],
        ]

class CreateIntroducerOptions(NoDefaultBasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to create the introducer in"],
        ]

client_tac = """
# -*- python -*-

from allmydata import client
from twisted.application import service

c = client.Client()

application = service.Application("allmydata_client")
c.setServiceParent(application)
"""

introducer_tac = """
# -*- python -*-

from allmydata import introducer
from twisted.application import service

c = introducer.IntroducerNode()

application = service.Application("allmydata_introducer")
c.setServiceParent(application)
"""

def create_client(basedir, config, out=sys.stdout, err=sys.stderr):
    if os.path.exists(basedir):
        if os.listdir(basedir):
            print >>err, "The base directory already exists: %s" % basedir
            print >>err, "To avoid clobbering anything, I am going to quit now"
            print >>err, "Please use a different directory, or delete this one"
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    f = open(os.path.join(basedir, "tahoe-client.tac"), "w")
    f.write(client_tac)
    f.close()
    if config.get('webport', "none").lower() != "none":
        f = open(os.path.join(basedir, "webport"), "w")
        f.write(config['webport'] + "\n")
        f.close()
    # Create an empty my_private_dir.uri file, indicating that the node
    # should fill it with the URI after creating the directory.
    open(os.path.join(basedir, "my_private_dir.uri"), "w")
    print >>out, "client created in %s" % basedir
    print >>out, " please copy introducer.furl into the directory"

def create_introducer(basedir, config, out=sys.stdout, err=sys.stderr):
    if os.path.exists(basedir):
        if os.listdir(basedir):
            print >>err, "The base directory already exists: %s" % basedir
            print >>err, "To avoid clobbering anything, I am going to quit now"
            print >>err, "Please use a different directory, or delete this one"
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    f = open(os.path.join(basedir, "tahoe-introducer.tac"), "w")
    f.write(introducer_tac)
    f.close()
    print >>out, "introducer created in %s" % basedir

subCommands = [
    ["create-client", None, CreateClientOptions, "Create a client node."],
    ["create-introducer", None, CreateIntroducerOptions, "Create a introducer node."],

]

dispatch = {
    "create-client": create_client,
    "create-introducer": create_introducer,
    }
