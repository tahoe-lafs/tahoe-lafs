from .run_common import (
    RunOptions as _RunOptions,
    run,
)

__all__ = [
    "DaemonizeOptions",
    "daemonize",
]

class DaemonizeOptions(_RunOptions):
    subcommand_name = "daemonize"

def daemonize(config):
    return run(config)
