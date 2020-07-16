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
SUBUNITREPORTER_OUTPUT_PATH="$base/results.subunit2" trial --reporter subunitv2-file allmydata
subunit2junitxml < "$base/results.subunit2" > "$base/results.xml"
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
git diff "$tracking_filename"

exit $code
