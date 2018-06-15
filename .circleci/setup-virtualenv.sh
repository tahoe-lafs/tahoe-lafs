#!/bin/bash -e

# Set up the virtualenv as a non-root user so we can run the test suite as a
# non-root user.  See below.
sudo --set-home -u nobody virtualenv --python python2.7 /tmp/tests
sudo --set-home -u nobody /tmp/tests/bin/pip install tox codecov
# Get everything installed in it, too.
sudo --set-home -u nobody /tmp/tests/bin/tox -c /tmp/project/tox.ini --workdir /tmp --notest -e "${TAHOE_LAFS_TOX_ENVIRONMENT}" ${TAHOE_LAFS_TOX_ARGS}
