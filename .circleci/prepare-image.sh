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

# Most stuff is going to run as nobody.  Here's a helper to make sure nobody
# can access necessary files.
CHOWN_NOBODY="chown --recursive nobody:$(id --group nobody)"

# Avoid the /nonexistent home directory in nobody's /etc/passwd entry.
usermod --home /tmp/nobody nobody

# Grant read access to nobody, the user which will eventually try to test this
# checkout.
${CHOWN_NOBODY} "${PROJECT_ROOT}"

# Create a place for some wheels to live.
mkdir "${WHEELHOUSE_PATH}"
${CHOWN_NOBODY} "${WHEELHOUSE_PATH}"

sudo --set-home -u nobody "${PROJECT_ROOT}"/.circleci/create-virtualenv.sh "${WHEELHOUSE_PATH}" "${BOOTSTRAP_VENV}"
sudo --set-home -u nobody "${PROJECT_ROOT}"/.circleci/populate-wheelhouse.sh "${WHEELHOUSE_PATH}" "${BOOTSTRAP_VENV}" "${PROJECT_ROOT}"
