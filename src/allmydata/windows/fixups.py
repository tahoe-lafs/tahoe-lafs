# This code isn't loadable or sensible except on Windows.  Importers all know
# this and are careful.  Normally I would just let an import error from ctypes
# explain any mistakes but Mypy also needs some help here.  This assert
# explains to it that this module is Windows-only.  This prevents errors about
# ctypes.windll and such which only exist when running on Windows.
#
# Beware of the limitations of the Mypy AST analyzer.  The check needs to take
# exactly this form or it may not be recognized.
#
# https://mypy.readthedocs.io/en/stable/common_issues.html?highlight=platform#python-version-and-system-platform-checks
import sys
assert sys.platform == "win32"

# <https://msdn.microsoft.com/en-us/library/ms680621%28VS.85%29.aspx>
from win32api import (
    SetErrorMode,
)
from win32con import (
    SEM_FAILCRITICALERRORS,
    SEM_NOOPENFILEERRORBOX,
)

# Keep track of whether `initialize` has run so we don't do any of the
# initialization more than once.
_done = False


def initialize():
    global _done
    import sys
    if sys.platform != "win32" or _done:
        return True
    _done = True

    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX)
