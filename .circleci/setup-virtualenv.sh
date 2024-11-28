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

# The filesystem location of the wheelhouse which we'll populate with wheels
# for all of our dependencies.
WHEELHOUSE_PATH="$1"
shift

TAHOE_LAFS_TOX_ENVIRONMENT=$1
shift

TAHOE_LAFS_TOX_ARGS=$1
shift || :

# Tell pip where it can find any existing wheels.
##export PIP_FIND_LINKS="file://${WHEELHOUSE_PATH}"
##export PIP_NO_INDEX="1"

# Get everything else installed in it, too.
"${BOOTSTRAP_VENV}"/bin/tox \
     -c "${PROJECT_ROOT}"/tox.ini \
     --workdir /tmp/tahoe-lafs.tox \
     --notest \
     -e "${TAHOE_LAFS_TOX_ENVIRONMENT}" \
     ${TAHOE_LAFS_TOX_ARGS}
