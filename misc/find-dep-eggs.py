#! /usr/bin/python

import os.path, sys

path = []
if sys.platform == 'win32':
    support_lib = "support/Lib/site-packages"
else:
    pyver = "python%d.%d" % (sys.version_info[:2])
    support_lib = "support/lib/%s/site-packages" % pyver

if os.path.exists(support_lib):
    for fn in os.listdir(support_lib):
        if fn.endswith(".egg"):
            path.append(os.path.abspath(os.path.join(support_lib, fn)))

# We also need to include .egg's in the CWD, because if there is an .egg there
# then "make build-deps" will take that as satisfying its requirements.
for fn in os.listdir("."):
    if fn.endswith(".egg"):
        path.append(os.path.abspath(os.path.join(os.getcwd(), fn)))

print os.pathsep.join(path)
