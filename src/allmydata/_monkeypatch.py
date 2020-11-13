"""
Monkey-patching of third party libraries.

Ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from warnings import catch_warnings


def patch():
    """Path third-party libraries to make Tahoe-LAFS work."""
    # Make sure Foolscap always get native strings passed to method names in callRemote.
    # This can be removed when any one of the following happens:
    #
    # 1. Tahoe-LAFS on Python 2 switches to version of Foolscap that fixes
    #    https://github.com/warner/foolscap/issues/72
    # 2. Foolscap is dropped as a dependency.
    # 3. Tahoe-LAFS drops Python 2 support.

    if not PY2:
        # Python 3 doesn't need to monkey patch Foolscap
        return

    # We need to suppress warnings so as to prevent unexpected output from
    # breaking some integration tests.
    with catch_warnings(record=True):
        # Only tested with this version; ensure correctness with new releases,
        # and then either update the assert or hopefully drop the monkeypatch.
        from foolscap import __version__
        assert __version__ == "0.13.1", "Wrong version %s of Foolscap" % (__version__,)

        from foolscap.referenceable import RemoteReference
        original_getMethodInfo = RemoteReference._getMethodInfo

        def _getMethodInfo(self, name):
            if isinstance(name, str):
                name = name.encode("utf-8")
            return original_getMethodInfo(self, name)
        RemoteReference._getMethodInfo = _getMethodInfo
