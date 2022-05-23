"""
Ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401


from allmydata.introducer.server import create_introducer

# apparently need to support "old .tac files" that may have
# "allmydata.introducer.IntroducerNode" burned in -- don't use this in
# new code
from allmydata.introducer.server import _IntroducerNode as IntroducerNode

# hush pyflakes
__all__ = (
    "create_introducer",
    "IntroducerNode",
)
