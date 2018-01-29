
# This is for compatibilty with old .tac files, which reference
# allmydata.introducer.IntroducerNode

from allmydata.introducer.server import create_introducer
# apparently need to support "old .tac files" that may have this name burned in
# don't use this in new code
from allmydata.introducer.server import _IntroducerNode as IntroducerNode

# hush pyflakes
_unused = [create_introducer, IntroducerNode]
del _unused
