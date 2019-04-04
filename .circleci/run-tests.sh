#!/bin/bash

# https://vaneyckt.io/posts/safer_bash_scripts_with_set_euxo_pipefail/
set -euxo pipefail

# The filesystem location of the root of a virtualenv we can use to get/build
# wheels.
BOOTSTRAP_VENV="$1"
shift

# The filesystem location of the root of the project source.  We need this to
# know what wheels to get/build, of course.
PROJECT_ROOT="$1"
shift

ARTIFACTS=$1
shift

TAHOE_LAFS_TOX_ENVIRONMENT=$1
shift

TAHOE_LAFS_TOX_ARGS=$1
shift || :

if [ -n "${ARTIFACTS}" ]; then
    # If given an artifacts path, prepare to have some artifacts created
    # there.  The integration tests don't produce any artifacts; that is the
    # case where we expect not to end up here.

    # Make sure we can actually write things to this directory.
    sudo --user nobody mkdir -p "${ARTIFACTS}"

    SUBUNIT2="${ARTIFACTS}"/results.subunit2

    # Use an intermediate directory here because CircleCI extracts some label
    # information from its name.
    JUNITXML="${ARTIFACTS}"/junit/unittests/results.xml
fi

# Run the test suite as a non-root user.  This is the expected usage some
# small areas of the test suite assume non-root privileges (such as unreadable
# files being unreadable).
#
# Also run with /tmp as a workdir because the non-root user won't be able to
# create the tox working filesystem state in the source checkout because it is
# owned by root.
#
# Send the output directly to a file because transporting the binary subunit2
# via tox and then scraping it out is hideous and failure prone.
export SUBUNITREPORTER_OUTPUT_PATH="${SUBUNIT2}"
export TAHOE_LAFS_TRIAL_ARGS="--reporter=subunitv2-file --rterrors"
export PIP_NO_INDEX="1"

${BOOTSTRAP_VENV}/bin/tox \
    -c ${PROJECT_ROOT}/tox.ini \
    --workdir /tmp/tahoe-lafs.tox \
    -e "${TAHOE_LAFS_TOX_ENVIRONMENT}" \
    ${TAHOE_LAFS_TOX_ARGS}

if [ -n "${ARTIFACTS}" ]; then
    # Create a junitxml results area.
    mkdir -p "$(dirname "${JUNITXML}")"
    ${BOOTSTRAP_VENV}/bin/subunit2junitxml < "${SUBUNIT2}" > "${JUNITXML}"
fi
