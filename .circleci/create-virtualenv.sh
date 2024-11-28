#!/bin/bash

# https://vaneyckt.io/posts/safer_bash_scripts_with_set_euxo_pipefail/
set -euxo pipefail

# The filesystem location of the wheelhouse which we'll populate with wheels
# for all of our dependencies.
WHEELHOUSE_PATH="$1"
shift

# The filesystem location of the root of a virtualenv we can use to get/build
# wheels.
BOOTSTRAP_VENV="$1"
shift

# The basename of the Python executable (found on PATH) that will be used with
# this image.  This lets us create a virtualenv that uses the correct Python.
PYTHON="$1"
shift

# Set up the virtualenv as a non-root user so we can run the test suite as a
# non-root user.  See below.
virtualenv --python "${PYTHON}" "${BOOTSTRAP_VENV}"

# For convenience.
PIP="${BOOTSTRAP_VENV}/bin/pip"

# Tell pip where it can find any existing wheels.
##export PIP_FIND_LINKS="file://${WHEELHOUSE_PATH}"

# Get "certifi" to avoid bug #2913. Basically if a `setup_requires=...` causes
# a package to be installed (with setuptools) then it'll fail on certain
# platforms (travis's OX-X 10.12, Slackware 14.2) because PyPI's TLS
# requirements (TLS >= 1.2) are incompatible with the old TLS clients
# available to those systems.  Installing it ahead of time (with pip) avoids
# this problem.  Make sure this step comes before any other attempts to
# install things using pip!
"${PIP}" install certifi

# Get a new, awesome version of pip and setuptools.  For example, the
# distro-packaged virtualenv's pip may not know about wheels.  Get the newer
# version of pip *first* in case we have a really old one now which can't even
# install setuptools properly.
"${PIP}" install --upgrade pip

# setuptools 45 requires Python 3.5 or newer.  Even though we upgraded pip
# above, it may still not be able to get us a compatible version unless we
# explicitly ask for one.
"${PIP}" install --upgrade setuptools wheel

# Just about every user of this image wants to use tox from the bootstrap
# virtualenv so go ahead and install it now.
"${PIP}" install "tox~=4.0"
