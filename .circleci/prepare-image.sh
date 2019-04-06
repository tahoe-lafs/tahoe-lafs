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

sudo --set-home -u nobody "${PROJECT_ROOT}"/.circleci/create-virtualenv.sh "${WHEELHOUSE_PATH}" "${BOOTSTRAP_VENV}"
sudo --set-home -u nobody "${PROJECT_ROOT}"/.circleci/populate-wheelhouse.sh "${WHEELHOUSE_PATH}" "${BOOTSTRAP_VENV}" "${PROJECT_ROOT}"
