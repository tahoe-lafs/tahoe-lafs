"""Foolscap"""

__version__ = "0.1.5"

# here are the primary entry points
from foolscap.pb import Tub, UnauthenticatedTub, getRemoteURL_TCP

# names we import so that others can reach them as foolscap.foo
from foolscap.remoteinterface import RemoteInterface
from foolscap.referenceable import Referenceable, SturdyRef
from foolscap.copyable import Copyable, RemoteCopy, registerRemoteCopy
from foolscap.copyable import registerCopier, registerRemoteCopyFactory
from foolscap.ipb import DeadReferenceError
from foolscap.tokens import BananaError
from foolscap import schema # necessary for the adapter_hooks side-effect
# TODO: Violation?

# hush pyflakes
_unused = [
    Tub, UnauthenticatedTub, getRemoteURL_TCP,
    RemoteInterface,
    Referenceable, SturdyRef,
    Copyable, RemoteCopy, registerRemoteCopy,
    registerCopier, registerRemoteCopyFactory,
    DeadReferenceError,
    BananaError,
    schema,
    ]
del _unused
