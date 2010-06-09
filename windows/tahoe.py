from allmydata.util import pkgresutil # override the pkg_resources zip provider for py2exe deployment
pkgresutil.install() # this is done before nevow is imported by depends
import depends # import dependencies so that py2exe finds them
_junk = depends # appease pyflakes

import sys
from allmydata.scripts import runner

sys.exit(runner(install_node_control=False))