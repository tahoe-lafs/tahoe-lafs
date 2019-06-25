#!/bin/bash

# https://vaneyckt.io/posts/safer_bash_scripts_with_set_euxo_pipefail/
set -euxo pipefail

# Basic Python packages that you just need to have around to do anything,
# practically speaking.
BASIC_DEPS="pip wheel"

# Python packages we need to support the test infrastructure.  *Not* packages
# Tahoe-LAFS itself (implementation or test suite) need.
TEST_DEPS="tox codecov"

# Python packages we need to generate test reports for CI infrastructure.
# *Not* packages Tahoe-LAFS itself (implement or test suite) need.
REPORTING_DEPS="python-subunit junitxml subunitreporter"

# The filesystem location of the wheelhouse which we'll populate with wheels
# for all of our dependencies.
WHEELHOUSE_PATH="$1"
shift

# The filesystem location of the root of a virtualenv we can use to get/build
# wheels.
BOOTSTRAP_VENV="$1"
shift

# The filesystem location of the root of the project source.  We need this to
# know what wheels to get/build, of course.
PROJECT_ROOT="$1"
shift

# For convenience.
PIP="${BOOTSTRAP_VENV}/bin/pip"

# Tell pip where it can find any existing wheels.
export PIP_FIND_LINKS="file://${WHEELHOUSE_PATH}"

# Populate the wheelhouse, if necessary.
"${PIP}" \
    wheel \
    --wheel-dir "${WHEELHOUSE_PATH}" \
    "${PROJECT_ROOT}"[test,tor,i2p] \
    ${BASIC_DEPS} \
    ${TEST_DEPS} \
    ${REPORTING_DEPS} \
    https://github.com/exarkun/pyutil/archive/good-version.zip#egg=pyutil    # XXX see if these changes fix pyutil for pypy


# Not strictly wheelhouse population but ... Note we omit basic deps here.
# They're in the wheelhouse if Tahoe-LAFS wants to drag them in but it will
# have to ask.
"${PIP}" \
    install \
    ${TEST_DEPS} \
    ${REPORTING_DEPS}
