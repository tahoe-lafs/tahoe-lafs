from .tahoe_daemonize import daemonize, DaemonizeOptions


class RunOptions(DaemonizeOptions):
    subcommand_name = "run"


def run(config):
    config.twistd_args = config.twistd_args + ("--nodaemon",)
    return daemonize(config)
