#!/bin/bash -e

TAHOE_LAFS_TOX_ENVIRONMENT=$1
shift

TAHOE_LAFS_TOX_ARGS=$1
shift || :

# Set up the virtualenv as a non-root user so we can run the test suite as a
# non-root user.  See below.
sudo --set-home -u nobody virtualenv --python python2.7 /tmp/tests

# Slackware has non-working SSL support in setuptools until certifi is
# installed.  SSL support in setuptools is needed in case packages use
# `setup_requires` which gets satisfied by setuptools instead of by pip.
# txi2p (vcversioner) is one such package.  Twisted (incremental) is another.
sudo --set-home -u nobody /tmp/tests/bin/pip install tox codecov

# Get everything else installed in it, too.
sudo --set-home -u nobody /tmp/tests/bin/tox -c /tmp/project/tox.ini --workdir /tmp --notest -e "${TAHOE_LAFS_TOX_ENVIRONMENT}" ${TAHOE_LAFS_TOX_ARGS}
