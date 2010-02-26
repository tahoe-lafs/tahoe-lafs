
# This is for compatibilty with old .tac files, which reference
# allmydata.introducer.IntroducerNode

from allmydata.introducer.server import IntroducerNode

# hush pyflakes
_unused = [IntroducerNode]
del _unused
