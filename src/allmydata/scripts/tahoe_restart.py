from __future__ import print_function

from .tahoe_start import StartOptions, start
from .tahoe_stop import stop, COULD_NOT_STOP


class RestartOptions(StartOptions):
    subcommand_name = "restart"


def restart(config):
    print("'tahoe restart' is deprecated; see 'tahoe run'")
    stderr = config.stderr
    rc = stop(config)
    if rc == COULD_NOT_STOP:
        print("ignoring couldn't-stop", file=stderr)
        rc = 0
    if rc:
        print("not restarting", file=stderr)
        return rc
    return start(config)
