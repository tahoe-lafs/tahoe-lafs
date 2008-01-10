import depends # import dependencies so that py2exe finds them
_junk = depends # appease pyflakes

from allmydata.scripts import runner
runner.run(install_node_control=False)
