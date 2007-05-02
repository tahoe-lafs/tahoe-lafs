#!/usr/bin/python

import sys
from distutils.core import setup
from foolscap import __version__

if __name__ == '__main__':
    setup(
        name="foolscap",
        version=__version__,
        description="Foolscap contains an RPC protocol for Twisted.",
        author="Brian Warner",
        author_email="warner@twistedmatrix.com",
        url="http://twistedmatrix.com/trac/wiki/FoolsCap",
        license="MIT",
        long_description="""\
Foolscap (aka newpb) is a new version of Twisted's native RPC protocol, known
as 'Perspective Broker'. This allows an object in one process to be used by
code in a distant process. This module provides data marshaling, a remote
object reference system, and a capability-based security model.
""",
        packages=["foolscap", "foolscap/slicers", "foolscap/test"],
        )
