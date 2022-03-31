
__all__ = [
    "interfaces",
]

from twisted.python.runtime import platform

if platform.isWindows():
    from ._windows_netifaces import interfaces
else:
    from ._posix_netifaces import interfaces
    
