"""
Decentralized storage grid.

community web site: U{https://tahoe-lafs.org/}
"""

__all__ = [
    "__version__",
    "__appname__",
    "__full_version__",
]

__appname__ = "tahoe-lafs"
from allmydata._version import __version__
__full_version__ = f"{__appname__}/{__version__}"

# Install Python 3 module locations in Python 2:
from future import standard_library
standard_library.install_aliases()


# Monkey-patch 3rd party libraries:
from ._monkeypatch import patch
patch()
del patch


# On Python 3, turn BytesWarnings into exceptions. This can have potential
# production impact... if BytesWarnings are actually present in the codebase.
# Given that this has been enabled before Python 3 Tahoe-LAFS was publicly
# released, no such code should exist, and this will ensure it doesn't get
# added either.
#
# Also note that BytesWarnings only happen if Python is run with -b option, so
# in practice this should only affect tests.
import warnings
# Error on BytesWarnings, to catch things like str(b""), but only for
# allmydata code.
warnings.filterwarnings("error", category=BytesWarning, module=".*allmydata.*")
