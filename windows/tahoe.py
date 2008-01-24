import pkgreshook # override the pkg_resources zip provider for py2exe deployment
pkgreshook.install() # this is done before nevow is imported by depends
import depends # import dependencies so that py2exe finds them
_junk = depends # appease pyflakes

from allmydata.scripts import runner
runner.run(install_node_control=False)
