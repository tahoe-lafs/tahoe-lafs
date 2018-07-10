#!/bin/bash -e

TAHOE_LAFS_TOX_ENVIRONMENT=$1
shift

TAHOE_LAFS_TOX_ARGS=$1
shift || :

# Set up the virtualenv as a non-root user so we can run the test suite as a
# non-root user.  See below.
sudo --set-home -u nobody virtualenv --python python2.7 /tmp/tests

# Get "certifi" to avoid bug #2913. Basically if a `setup_requires=...` causes
# a package to be installed (with setuptools) then it'll fail on certain
# platforms (travis's OX-X 10.12, Slackware 14.2) because PyPI's TLS
# requirements (TLS >= 1.2) are incompatible with the old TLS clients
# available to those systems.  Installing it ahead of time (with pip) avoids
# this problem.  Make sure this step comes before any other attempts to
# install things using pip!
sudo --set-home -u nobody /tmp/tests/bin/pip install certifi

# Get a new, awesome version of pip and setuptools.  For example, the
# distro-packaged virtualenv's pip may not know about wheels.
sudo --set-home -u nobody /tmp/tests/bin/pip install --upgrade pip setuptools wheel

# Populate the wheelhouse, if necessary.
sudo --set-home -u nobody /tmp/tests/bin/pip -vvv \
     wheel \
     --wheel-dir "${WHEELHOUSE_PATH}" \
     /tmp/project


# Python packages we need to support the test infrastructure.  *Not* packages
# Tahoe-LAFS itself (implementation or test suite) need.
TEST_DEPS="tox codecov"

# Python packages we need to generate test reports for CI infrastructure.
# *Not* packages Tahoe-LAFS itself (implement or test suite) need.
REPORTING_DEPS="python-subunit junitxml subunitreporter"

sudo --set-home -u nobody /tmp/tests/bin/pip install ${TEST_DEPS} ${REPORTING_DEPS}

# Get everything else installed in it, too.
sudo --set-home -u nobody /tmp/tests/bin/tox \
     -c /tmp/project/tox.ini \
     --workdir /tmp/tahoe-lafs.tox \
     --notest \
     -e "${TAHOE_LAFS_TOX_ENVIRONMENT}" \
     ${TAHOE_LAFS_TOX_ARGS}
