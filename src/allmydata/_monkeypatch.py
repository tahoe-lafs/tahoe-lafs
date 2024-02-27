"""
Monkey-patching of third party libraries.

Ported to Python 3.
"""

from future.utils import PY2



def patch():
    """Path third-party libraries to make Tahoe-LAFS work."""

    if not PY2:
        # Python 3 doesn't need to monkey patch Foolscap
        return
