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
sudo --set-home -u nobody /tmp/tests/bin/tox \
     -c /tmp/project/tox.ini \
     --result-json /tmp/tox-result.json \
     --workdir /tmp \
     -e "${TAHOE_LAFS_TOX_ENVIRONMENT}" \
     --reporter=subunit ${TAHOE_LAFS_TOX_ARGS}

# Extract the test process output which should be subunit1-format.
cat /tmp/tox-result.json | /tmp/tests/bin/python -c '
from json import load
from sys import stdin
result = load(sys.stdin)
messy_output = result["testenvs"]["py27"]["test"][-1]["output"]
sys.stdout.write(messy_output.split("\n", 3)[3].strip())
' > /tmp/test-result.subunit1

# Convert the subunit1 data to junitxml which CircleCI can ingest.
mkdir -p /tmp/junit
subunit-1to2 < /tmp/test-result.subunit1 | subunit2junitxml > /tmp/junit/results.junitxml
