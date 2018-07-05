#!/bin/bash -e

TAHOE_LAFS_TOX_ENVIRONMENT=$1
shift

TAHOE_LAFS_TOX_ARGS=$1
shift || :

# Set up the virtualenv as a non-root user so we can run the test suite as a
# non-root user.  See below.
sudo --set-home -u nobody virtualenv --python python2.7 /tmp/tests

# Python packages we need to support the test infrastructure.  *Not* packages
# Tahoe-LAFS itself (implementation or test suite) need.
TEST_DEPS="tox codecov"

# Python packages we need to generate test reports for CI infrastructure.
# *Not* packages Tahoe-LAFS itself (implement or test suite) need.
REPORTING_DEPS="python-subunit junitxml"

sudo --set-home -u nobody /tmp/tests/bin/pip install ${TEST_DEPS} ${REPORTING_DEPS}

# Get everything else installed in it, too.
sudo --set-home -u nobody /tmp/tests/bin/tox -c /tmp/project/tox.ini --workdir /tmp --notest -e "${TAHOE_LAFS_TOX_ENVIRONMENT}" ${TAHOE_LAFS_TOX_ARGS}
