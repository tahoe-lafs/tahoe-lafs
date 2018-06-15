#!/bin/bash -eo pipefail

PROJECT=$1
shift

# Avoid the /nonexistent home directory in nobody's /etc/passwd entry.
usermod --home /tmp/nobody nobody

# Grant read access to nobody, the user which will eventually try to test this
# checkout.
mv "${PROJECT}" /tmp/project

# Python build/install toolchain wants to write to the source checkout, too.
chown --recursive nobody:nogroup /tmp/project

apt-get --quiet --yes install \
    sudo \
    build-essential \
    python2.7 \
    python2.7-dev \
    libffi-dev \
    libssl-dev \
    libyaml-dev \
    virtualenv \
    ${EXTRA_PACKAGES}
