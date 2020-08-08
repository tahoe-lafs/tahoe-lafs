#!/usr/bin/env bash
set -euxo pipefail
tracking_filename="ratchet-passing"

# Start somewhere predictable.
cd "$(dirname $0)"
base=$(pwd)

# Actually, though, trial outputs some things that are only gitignored in the project root.
cd "../.."

# Since both of the next calls are expected to exit non-0, relax our guard.
set +e
trial --temp-directory /tmp/_trial_temp.ratchet --reporter subunitv2-file allmydata
find "${SUBUNITREPORTER_OUTPUT_PATH}"
subunit2junitxml < "${SUBUNITREPORTER_OUTPUT_PATH}" > "$base/results.xml"
set -e

# Okay, now we're clear.
cd "$base"

# Make sure ratchet.py itself is clean.
python3 -m doctest ratchet.py

# Now see about Tahoe-LAFS (also expected to fail) ...
set +e
python3 ratchet.py up results.xml "$tracking_filename"
code=$?
set -e

# Emit a diff of the tracking file, to aid in the situation where changes are
# not discovered until CI (where TERM might `dumb`).
if [ $TERM = 'dumb' ]; then
  export TERM=ansi
fi

echo "The ${tracking_filename} diff is:"
echo "================================="
# "git diff" gets pretty confused in this execution context when trying to
# write to stdout.  Somehow it fails with SIGTTOU.
git diff -- "${tracking_filename}" > tracking.diff
cat tracking.diff
echo "================================="

echo "Exiting with code ${code} from ratchet.py."
exit ${code}
