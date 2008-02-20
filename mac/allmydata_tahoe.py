from allmydata.util import pkgresutil # override the pkg_resources zip provider for py2app deployment
pkgresutil.install() # this is done before nevow is imported by depends
import depends # import dependencies so that py2exe finds them
_junk = depends # appease pyflakes

import sys

from twisted.python import usage

class ReplOptions(usage.Options):
    pass

def repl(config, stdout, stderr):
    import code
    return code.interact()

class DbgRunnerExtension(object):
    subCommands = [
        ["dbgrepl", None, ReplOptions, "Open a python interpreter"],
        ]
    dispatch = {
        "dbgrepl": repl,
        }

class FuseOptions(usage.Options):
    def parseOptions(self, args):
        self.args = args

def fuse(config, stdout, stderr):
    import macfuse.tahoefuse
    macfuse.tahoefuse.main(config.args)

class FuseRunnerExtension(object):
    subCommands = [
        ["fuse", None, FuseOptions, "Mount a filesystem via fuse"],
        ]
    dispatch = {
        "fuse": fuse,
        }

def main(argv):
    if len(argv) == 1:
        # then we were given no args; do default mac node startup
        from allmydata.gui.macapp import run_macapp
        sys.exit(run_macapp())
    else:
        # given any cmd line args, do 'tahoe' cli behaviour
        from allmydata.scripts import runner
        #runner_extensions = [DbgRunnerExtension, FuseRunnerExtension, ]
        runner_extensions = [FuseRunnerExtension, ]
        sys.exit(runner.runner(argv[1:],
                               install_node_control=False,
                               additional_commands=runner_extensions,
                               ))

if __name__ == '__main__':
    main(sys.argv)

