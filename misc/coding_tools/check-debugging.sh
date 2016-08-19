#!/bin/bash

OUTPUT=$(grep -R '\.setDebugging(True)' src/allmydata)

if [[ -n $OUTPUT ]] ; then
    echo "Do not use defer.setDebugging(True) in production:"
    echo $OUTPUT
    exit 1
fi
