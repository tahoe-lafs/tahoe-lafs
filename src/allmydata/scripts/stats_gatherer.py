
import os, sys
from allmydata.scripts.common import BasedirMixin, BaseOptions
from allmydata.util.assertutil import precondition
from allmydata.util.encodingutil import listdir_unicode, quote_output

class CreateStatsGathererOptions(BasedirMixin, BaseOptions):
    default_nodedir = None

    optParameters = [
        ["node-directory", "d", None, "Specify which directory the stats-gatherer should be created in. [no default]"],
    ]


stats_gatherer_tac = """
# -*- python -*-

from allmydata import stats
from twisted.application import service

verbose = True
g = stats.StatsGathererService(verbose=verbose)

application = service.Application('allmydata_stats_gatherer')
g.setServiceParent(application)
"""


def create_stats_gatherer(config, out=sys.stdout, err=sys.stderr):
    basedir = config['basedir']
    # This should always be called with an absolute Unicode basedir.
    precondition(isinstance(basedir, unicode), basedir)

    if os.path.exists(basedir):
        if listdir_unicode(basedir):
            print >>err, "The base directory %s is not empty." % quote_output(basedir)
            print >>err, "To avoid clobbering anything, I am going to quit now."
            print >>err, "Please use a different directory, or empty this one."
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    f = open(os.path.join(basedir, "tahoe-stats-gatherer.tac"), "wb")
    f.write(stats_gatherer_tac)
    f.close()
    return 0

subCommands = [
    ["create-stats-gatherer", None, CreateStatsGathererOptions, "Create a stats-gatherer service."],
]

dispatch = {
    "create-stats-gatherer": create_stats_gatherer,
    }


