#!/bin/bash -e

ARTIFACTS=$1
shift

TAHOE_LAFS_TOX_ENVIRONMENT=$1
shift

TAHOE_LAFS_TOX_ARGS=$1
shift || :

# Make sure we can actually write things to this directory.
sudo --user nobody mkdir -p "${ARTIFACTS}"

# Run the test suite as a non-root user.  This is the expected usage some
# small areas of the test suite assume non-root privileges (such as unreadable
# files being unreadable).
#
# Also run with /tmp as a workdir because the non-root user won't be able to
# create the tox working filesystem state in the source checkout because it is
# owned by root.
sudo TAHOE_LAFS_TRIAL_ARGS="--reporter=subunit" \
     --set-home \
     --user nobody \
     /tmp/tests/bin/tox \
     -c /tmp/project/tox.ini \
     --result-json "${ARTIFACTS}"/tox-result.json \
     --workdir /tmp/tahoe-lafs.tox \
     -e "${TAHOE_LAFS_TOX_ENVIRONMENT}" \
     ${TAHOE_LAFS_TOX_ARGS}

TOX_JSON="${ARTIFACTS}"/tox-result.json
SUBUNIT1="${ARTIFACTS}"/results.subunit1
SUBUNIT2="${ARTIFACTS}"/results.subunit2

# Use an intermediate directory here because CircleCI extracts some label
# information from its name.
JUNITXML="${ARTIFACTS}"/junit/unittests/results.xml

# Extract the test process output which should be subunit1-format.
/tmp/tests/bin/python -c '
from json import load
from sys import stdin, stdout, argv
result = load(stdin)
for environ in argv[1].split(","):
    messy_output = result["testenvs"][environ]["test"][-1]["output"]
    stdout.write(messy_output.split("\n", 3)[3].strip() + "\n")
' "${TAHOE_LAFS_TOX_ENVIRONMENT}" < "${TOX_JSON}" > "${SUBUNIT1}"

# Upgrade subunit version because subunit2junitxml only works on subunit2
/tmp/tests/bin/subunit-1to2 < "${SUBUNIT1}" > "${SUBUNIT2}"

# Create a junitxml results area.
mkdir -p "$(dirname "${JUNITXML}")"
/tmp/tests/bin/subunit2junitxml < "${SUBUNIT2}" > "${JUNITXML}"
