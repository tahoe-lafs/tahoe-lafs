#!/usr/bin/env bash
base="$(dirname $0)"

# trial outputs some things that are only git-ignored in the root, so don't cd quite yet ...
trial --reporter subunitv2 allmydata | subunit2junitxml > "$base/results.xml"

# Okay, now we're clear.
cd "$base"
python3 ratchet.py up results.xml passing
