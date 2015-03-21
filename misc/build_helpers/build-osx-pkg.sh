#!/bin/sh

APPNAME=$1
VERSION=$2
PWD=`pwd`

# The editing of allmydata-tahoe.egg-link and easy-install.pth files
# (*in-place*) ensures that we reference the source at the correct path,
# removing the hard-coded local source tree directory names.
#
find support -name $APPNAME.egg-link -execdir sh -c "echo >> {}; echo /Applications/tahoe.app/src >> {}" \;
find support -name easy-install.pth -execdir sed -i.bak 's|^.*/src$|../../../../src|' '{}' \;

# create component pkg
pkgbuild --root $PWD \
         --identifier com.leastauthority.tahoe \
         --version $VERSION \
         --ownership recommended \
         --install-location /Applications/tahoe.app \
         --scripts $PWD/misc/build_helpers/osx/scripts \
         tahoe-lafs.pkg

# create product archive
productbuild --distribution $PWD/misc/build_helpers/osx/Distribution.xml \
             --package-path . \
             tahoe-lafs-$VERSION-osx.pkg

# remove intermediate pkg
rm -f tahoe-lafs.pkg
