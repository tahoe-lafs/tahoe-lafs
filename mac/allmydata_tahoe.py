from allmydata.util import pkgresutil # override the pkg_resources zip provider for py2app deployment
pkgresutil.install() # this is done before nevow is imported by depends
import depends # import dependencies so that py2exe finds them
_junk = depends # appease pyflakes

import sys

def main(argv):
    if len(argv) == 1:
        # then we were given no args; do default mac node startup
        from allmydata.gui.macapp import run_macapp
        sys.exit(run_macapp())
    else:
        # given any cmd line args, do 'tahoe' cli behaviour
        from allmydata.scripts import runner
        sys.exit(runner.runner(argv[1:], install_node_control=False))

if __name__ == '__main__':
    main(sys.argv)

