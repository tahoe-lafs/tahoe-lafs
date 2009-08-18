#!/bin/bash
set -e

# $PYTHON and $ARCH must be set

if [ -z "$PYTHON" ]; then
    PYTHON=python
fi
if [ -z "$ARCH" ]; then
    echo "must set ARCH= before running this script"
    exit 1
fi

NAME=$($PYTHON setup.py --name)
VERSION=$($PYTHON setup.py --version)

# actually, it's the debchange using a different author than the
# debian/control Maintainer: entry that makes lintian think this is an NMU.
# Put "local package" on the first line of the changelog entry to supress
# this warning.
TARBALL=${NAME}-${VERSION}.tar.gz
DEBTARBALL=${NAME}_${VERSION}.orig.tar.gz
DEBDIR=build/debian/${NAME}-${VERSION}
$PYTHON setup.py sdist --formats=gztar
rm -rf build/debian
mkdir -p build/debian
cp dist/$TARBALL build/debian/$DEBTARBALL
(cd build/debian && tar xf $DEBTARBALL)
zcat misc/debian/$ARCH.diff.gz | (cd $DEBDIR && patch -p1)
chmod +x $DEBDIR/debian/rules
# We put "local package" on the first line of the changelog entry to suppress
# the lintian NMU warnings (since debchange's new entry's "author" will
# probably be different than the what the debian/control Maintainer: field
# says)
echo "updating version to $VERSION-1"
(cd $DEBDIR && debchange --newversion $VERSION-1 "local package: 'make deb' build")
(cd $DEBDIR && debuild -uc -us)

