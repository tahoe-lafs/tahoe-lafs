
import sys
from allmydata.scripts.common import NoDefaultBasedirOptions, create_basedir, NonEmptyBasedirException
from allmydata.util.encodingutil import quote_output


class CreateStatsGathererOptions(NoDefaultBasedirOptions):
    subcommand_name = "create-stats-gatherer"


def create_stats_gatherer(config, out=sys.stdout, err=sys.stderr):
    basedir = config['basedir']
    try:
        create_basedir(basedir, "stats-gatherer", err=err)
    except NonEmptyBasedirException:
        return -1

    print >>out, "Stats gatherer created in %s" % quote_output(basedir)
    return 0

subCommands = [
    ["create-stats-gatherer", None, CreateStatsGathererOptions, "Create a stats-gatherer service."],
]

dispatch = {
    "create-stats-gatherer": create_stats_gatherer,
    }


