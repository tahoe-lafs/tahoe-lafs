#!/bin/bash -e

TAHOE_LAFS_TOX_ENVIRONMENT=$1
shift

TAHOE_LAFS_TOX_ARGS=$1
shift || :

# Get everything else installed in it, too.
/tmp/tests/bin/tox \
     -c /tmp/project/tox.ini \
     --workdir /tmp/tahoe-lafs.tox \
     --notest \
     -e "${TAHOE_LAFS_TOX_ENVIRONMENT}" \
     ${TAHOE_LAFS_TOX_ARGS}
