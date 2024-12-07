#!/bin/bash

# https://vaneyckt.io/posts/safer_bash_scripts_with_set_euxo_pipefail/
set -euxo pipefail

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

# Populate the wheelhouse, if necessary.  zfec 1.5.3 can only be built with a
# UTF-8 environment so make sure we have one, at least for this invocation.
LANG="en_US.UTF-8" "${PIP}" \
    wheel \
    --wheel-dir "${WHEELHOUSE_PATH}" \
    "${PROJECT_ROOT}"[testenv] \
    "${PROJECT_ROOT}"[test] \
    "${PROJECT_ROOT}"[build]
