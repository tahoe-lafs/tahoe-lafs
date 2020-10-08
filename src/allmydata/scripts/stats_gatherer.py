from __future__ import print_function

import os

# Python 2 compatibility
from future.utils import PY2
if PY2:
    from future.builtins import str  # noqa: F401

from twisted.python import usage

from allmydata.scripts.common import NoDefaultBasedirOptions
from allmydata.scripts.create_node import write_tac
from allmydata.util.assertutil import precondition
from allmydata.util.encodingutil import listdir_unicode, quote_output
from allmydata.util import fileutil, iputil


class CreateStatsGathererOptions(NoDefaultBasedirOptions):
    subcommand_name = "create-stats-gatherer"
    optParameters = [
        ("hostname", None, None, "Hostname of this machine, used to build location"),
        ("location", None, None, "FURL connection hints, e.g. 'tcp:HOSTNAME:PORT'"),
        ("port", None, None, "listening endpoint, e.g. 'tcp:PORT'"),
        ]
    def postOptions(self):
        if self["hostname"] and (not self["location"]) and (not self["port"]):
            pass
        elif (not self["hostname"]) and self["location"] and self["port"]:
            pass
        else:
            raise usage.UsageError("You must provide --hostname, or --location and --port.")

    description = """
    Create a "stats-gatherer" service, which is a standalone process that
    collects and stores runtime statistics from many server nodes. This is a
    tool for operations personnel to keep track of free disk space, server
    load, and protocol activity, across a fleet of Tahoe storage servers.

    The "stats-gatherer" listens on a TCP port and publishes a Foolscap FURL
    by writing it into a file named "stats_gatherer.furl". You must copy this
    FURL into the servers' tahoe.cfg, as the [client] stats_gatherer.furl=
    entry. Those servers will then establish a connection to the
    stats-gatherer and publish their statistics on a periodic basis. The
    gatherer writes a summary JSON file out to disk after each update.

    The stats-gatherer listens on a configurable port, and writes a
    configurable hostname+port pair into the FURL that it publishes. There
    are two configuration modes you can use.

    * In the first, you provide --hostname=, and the service chooses its own
      TCP port number. If the host is named "example.org" and you provide
      --hostname=example.org, the node will pick a port number (e.g. 12345)
      and use location="tcp:example.org:12345" and port="tcp:12345".

    * In the second, you provide both --location= and --port=, and the
      service will refrain from doing any allocation of its own. --location=
      must be a Foolscap "FURL connection hint sequence", which is a
      comma-separated list of "tcp:HOSTNAME:PORTNUM" strings. --port= must be
      a Twisted server endpoint specification, which is generally
      "tcp:PORTNUM". So, if your host is named "example.org" and you want to
      use port 6789, you should provide --location=tcp:example.org:6789 and
      --port=tcp:6789. You are responsible for making sure --location= and
      --port= match each other.
    """


def create_stats_gatherer(config):
    err = config.stderr
    basedir = config['basedir']
    # This should always be called with an absolute Unicode basedir.
    precondition(isinstance(basedir, str), basedir)

    if os.path.exists(basedir):
        if listdir_unicode(basedir):
            print("The base directory %s is not empty." % quote_output(basedir), file=err)
            print("To avoid clobbering anything, I am going to quit now.", file=err)
            print("Please use a different directory, or empty this one.", file=err)
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    write_tac(basedir, "stats-gatherer")
    if config["hostname"]:
        portnum = iputil.allocate_tcp_port()
        location = "tcp:%s:%d" % (config["hostname"], portnum)
        port = "tcp:%d" % portnum
    else:
        location = config["location"]
        port = config["port"]
    fileutil.write(os.path.join(basedir, "location"), location+"\n")
    fileutil.write(os.path.join(basedir, "port"), port+"\n")
    return 0

subCommands = [
    ["create-stats-gatherer", None, CreateStatsGathererOptions, "Create a stats-gatherer service."],
]

dispatch = {
    "create-stats-gatherer": create_stats_gatherer,
    }


