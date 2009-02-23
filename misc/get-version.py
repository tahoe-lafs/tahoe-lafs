#!/usr/bin/env python

"""Determine the version number of the current tree.

This should be run after 'setup.py darcsver'. It will emit a single line of text
to stdout, either of the form '0.2.0' if this is a release tree (i.e. no patches
have been added since the last release tag), or '0.2.0-34' (if 34 patches have
been added since the last release tag). If the tree does not have a well-formed
version number, this will emit 'unknown'.

The version string thus calculated should exactly match the version string
determined by setup.py (when it creates eggs and source tarballs) and also
the version available in the code image when you do:

 from allmydata import __version__

"""

import os.path, re

def get_version():
    VERSIONFILE = "src/allmydata/_version.py"
    verstr = "unknown"
    if os.path.exists(VERSIONFILE):
        VSRE = re.compile("^verstr = ['\"]([^'\"]*)['\"]", re.M)
        verstrline = open(VERSIONFILE, "rt").read()
        mo = VSRE.search(verstrline)
        if mo:
            verstr = mo.group(1)
        else:
            raise ValueError("if version.py exists, it must be well-formed")

    return verstr

if __name__ == '__main__':
    verstr = get_version()
    print verstr

