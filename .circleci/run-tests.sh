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
mkdir /tmp/junit
cat /tmp/tox-result.json | /tmp/tests/bin/python -c '
from json import load
from sys import stdin, stdout, argv
result = load(stdin)
messy_output = result["testenvs"][argv[1]]["test"][-1]["output"]
stdout.write(messy_output.split("\n", 3)[3].strip())
' "${TAHOE_LAFS_TOX_ENVIRONMENT}" |
    /tmp/tests/bin/subunit-1to2 |
    /tmp/tests/bin/subunit2junitxml > /tmp/junit/results.xml
