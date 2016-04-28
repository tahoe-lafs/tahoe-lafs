#!/bin/sh

VERSION=`sh -c "cat src/allmydata/_version.py | grep verstr | head -n 1 | cut -d' ' -f 3" | sed "s/\"//g"`
PWD=`pwd`
TARGET="/Applications/tahoe.app"

virtualenv osx-venv
osx-venv/bin/pip install .

# The virtualenv contains all the dependencies we need, but the bin/python
# itself is not useful, nor is having it as the shbang line in the generated
# bin/tahoe executable. Replace bin/tahoe with a form that explicitly sets
# sys.path to the target directory (/Applications/tahoe.app). This isn't as
# isolated as a proper virtualenv would be (the system site-packages
# directory will still appear later in sys.path), but I think it ought to
# work.

rm osx-venv/bin/*
cat >osx-venv/bin/tahoe <<EOF
#!/usr/bin/env python
import sys, os.path
up = os.path.dirname
bintahoe = os.path.abspath(__file__)
appdir = up(up(bintahoe))
sitedir = os.path.join(appdir, "lib", "python2.7", "site-packages")
# usually "/Applications/tahoe.app/lib/python2.7/site-packages"
sys.path.insert(0, sitedir)
from allmydata.scripts.runner import run
run()
EOF
chmod +x osx-venv/bin/tahoe

# The venv has a .pth file which allows "import zope.interface" to work even
# though "zope" isn't really a package (it has no __init__.py). The venv's
# python has this site-packages/ on sys.path early enough to process the .pth
# file, and running tahoe with PYTHONPATH=...site-packages would also process
# it, but a simple sys.path.insert doesn't. This is the simplest hack I could
# find to fix it.

touch osx-venv/lib/python2.7/site-packages/zope/__init__.py

cp -r $PWD/misc/build_helpers/osx/Contents osx-venv/Contents

# create component pkg
pkgbuild --root osx-venv \
         --identifier com.leastauthority.tahoe \
         --version "$VERSION" \
         --ownership recommended \
         --install-location $TARGET \
         --scripts "$PWD/misc/build_helpers/osx/scripts" \
         tahoe-lafs.pkg

# create product archive
productbuild --distribution "$PWD/misc/build_helpers/osx/Distribution.xml" \
             --package-path . \
             "tahoe-lafs-$VERSION-osx.pkg"

# remove intermediate pkg
rm -f tahoe-lafs.pkg
