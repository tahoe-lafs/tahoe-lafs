#!/bin/bash -e

TAHOE_LAFS_TOX_ENVIRONMENT=$1
shift

TAHOE_LAFS_TOX_ARGS=$1
shift || :

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
     --result-json /tmp/tox-result.json \
     --workdir /tmp/tahoe-lafs.tox \
     -e "${TAHOE_LAFS_TOX_ENVIRONMENT}" \
     ${TAHOE_LAFS_TOX_ARGS}

# Extract the test process output which should be subunit1-format.
/tmp/tests/bin/python -c '
from json import load
from sys import stdin, stdout, argv
result = load(stdin)
for environ in argv[1].split(","):
    messy_output = result["testenvs"][environ]["test"][-1]["output"]
    stdout.write(messy_output.split("\n", 3)[3].strip() + "\n")
' "${TAHOE_LAFS_TOX_ENVIRONMENT}" < /tmp/tox-result.json > /tmp/results.subunit1

# Upgrade subunit version because subunit2junitxml only works on subunit2
/tmp/tests/bin/subunit-1to2 < /tmp/results.subunit1 > /tmp/results.subunit2

# Create a junitxml results area.  Put these results in a subdirectory of the
# ultimate location because CircleCI extracts some label information from the
# subdirectory name.
mkdir -p /tmp/artifacts/junit/unittests
/tmp/tests/bin/subunit2junitxml < /tmp/results.subunit2 > /tmp/artifacts/junit/unittests/results.xml
