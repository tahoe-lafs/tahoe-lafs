from .run_common import (
    RunOptions as _RunOptions,
    run,
)

__all__ = [
    "RunOptions",
    "run",
]

class RunOptions(_RunOptions):
    subcommand_name = "run"

    def postOptions(self):
        self.twistd_args += ("--nodaemon",)
