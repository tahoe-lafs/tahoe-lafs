#! /usr/bin/python

import sys, os.path
from distutils.util import get_platform

# because we use extension modules, we need a platform+version-specific
# libdir. If we were using a pure-python module, this would just be "lib".
plat_specifier = ".%s-%s" % (get_platform(), sys.version[0:3])
libdir = os.path.join("build", "lib" + plat_specifier)
if len(sys.argv) > 1 and sys.argv[1] == "-a":
    libdir = os.path.abspath(libdir)
print libdir
