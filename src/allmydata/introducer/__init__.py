# This is for compatibilty with old .tac files, which reference
# allmydata.introducer.IntroducerNode
from __future__ import print_function
from allmydata.introducer.server import _IntroducerNode as IntroducerNode

# hush pyflakes
_unused = [IntroducerNode]
del _unused
