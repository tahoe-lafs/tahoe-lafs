#! /usr/bin/python

import os.path, sys

pyver = "python%d.%d" % (sys.version_info[:2])

path = []
support_lib = "support/lib/%s/site-packages" % pyver
if os.path.exists(support_lib):
    for fn in os.listdir(support_lib):
        if fn.endswith(".egg"):
            path.append(os.path.abspath(os.path.join(support_lib, fn)))

print ":".join(path)
