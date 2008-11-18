
import os
from twisted.python import usage

class CreateStatsGathererOptions(usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to create the stats-gatherer in"],
        ]

    def parseArgs(self, basedir=None):
        if basedir is not None:
            assert self["basedir"] is None
            self["basedir"] = basedir


stats_gatherer_tac = """
# -*- python -*-

from allmydata import stats
from twisted.application import service

verbose = True
g = stats.StatsGathererService(verbose=verbose)

application = service.Application('allmydata_stats_gatherer')
g.setServiceParent(application)
"""

def create_stats_gatherer(config):
    out = config.stdout
    err = config.stderr
    basedir = config['basedir']
    if not basedir:
        print >>err, "a basedir was not provided, please use --basedir or -C"
        return -1
    if os.path.exists(basedir):
        if os.listdir(basedir):
            print >>err, "The base directory \"%s\", which is \"%s\" is not empty." % (basedir, os.path.abspath(basedir))
            print >>err, "To avoid clobbering anything, I am going to quit now."
            print >>err, "Please use a different directory, or empty this one."
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    f = open(os.path.join(basedir, "tahoe-stats-gatherer.tac"), "wb")
    f.write(stats_gatherer_tac)
    f.close()

subCommands = [
    ["create-stats-gatherer", None, CreateStatsGathererOptions, "Create a stats-gatherer service."],
]

dispatch = {
    "create-stats-gatherer": create_stats_gatherer,
    }


