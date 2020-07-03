#!/usr/bin/env bash
base="$(dirname $0)"
tracking="passing"

# trial outputs some things that are only git-ignored in the root, so don't cd quite yet ...
set +e
trial --reporter subunitv2 allmydata | subunit2junitxml > "$base/results.xml"
set -e

# Okay, now we're clear.
cd "$base"

# Make sure ratchet.py itself is clean.
python3 -m doctest ratchet.py

# Now see about Tahoe-LAFS ...
set +e
# P.S. Don't invoke as `python ratchet.py ...` because then Python swallows the
# exit code.
./ratchet.py up results.xml "$tracking"
code=$?
set -e

# Emit a diff of the tracking file, to aid in the situation where changes are
# not discovered until CI (where TERM might `dumb`).
if [ $TERM = 'dumb' ]; then
  export TERM=ansi
fi
git diff "$tracking"

exit $code
