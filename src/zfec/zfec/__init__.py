"""
zfec -- fast forward error correction library with Python interface

maintainer web site: U{http://allmydata.com/source/zfec}

zfec web site: U{http://allmydata.com/source/zfec}
"""

from util.version import Version

# For an explanation of what the parts of the version string mean,
# please see pyutil.version.
__version__ = Version("1.0.0a5-1-STABLE")

# Please put a URL or other note here which shows where to get the branch of
# development from which this version grew.
__sources__ = ["http://allmydata.com/source/zfec",]

from _fec import Encoder, Decoder, Error
import filefec

