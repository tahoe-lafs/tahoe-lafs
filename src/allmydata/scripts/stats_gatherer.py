
import os, sys

from allmydata.scripts.common import NoDefaultBasedirOptions
from allmydata.scripts.create_node import write_tac
from allmydata.util.assertutil import precondition
from allmydata.util.encodingutil import listdir_unicode, quote_output


class CreateStatsGathererOptions(NoDefaultBasedirOptions):
    subcommand_name = "create-stats-gatherer"


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
    write_tac(basedir, "stats-gatherer")
    return 0

subCommands = [
    ["create-stats-gatherer", None, CreateStatsGathererOptions, "Create a stats-gatherer service."],
]

dispatch = {
    "create-stats-gatherer": create_stats_gatherer,
    }


