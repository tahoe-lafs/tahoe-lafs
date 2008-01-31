#!/bin/sh -e

# this is called (with e.g. V=0.7.0-175) after a 'setup.py sdist' has been
# executed, so there will be a dist/allmydata-tahoe-${VER}.tar.gz present.

echo "creating tarballs for tahoe version '${V}'"

# we leave the original .tar.gz in place, put a decompressed copy in .tar,
# and then compress it with a number of other compressors.
gunzip -c -d dist/allmydata-tahoe-${V}.tar.gz >dist/allmydata-tahoe-${V}.tar

bzip2 -k dist/allmydata-tahoe-${V}.tar

# rzip comes from the 'rzip' package
rzip -k -9 dist/allmydata-tahoe-${V}.tar

# 7z comes from the 'p7zip-full' package
7z a dist/allmydata-tahoe-${V}.tar.7z dist/allmydata-tahoe-${V}.tar

# lrzip is destructive (no -k option)
# it is disabled because I cannot find a debian package for it. zooko, where
# did you find this thing?
#lrzip -M dist/allmydata-tahoe-${V}.tar
# since we disabled lrzip, we should remove the .tar file
rm dist/allmydata-tahoe-${V}.tar

#time rsync --partial --progress allmydata-tahoe-${V}.tar.* zooko@allmydata.org:/var/www/source/tahoe/

