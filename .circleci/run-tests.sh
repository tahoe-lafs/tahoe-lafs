#!/bin/sh

# Run the test suite as a non-root user.  This is the expected usage some
# small areas of the test suite assume non-root privileges (such as unreadable
# files being unreadable).
#
# Also run with /tmp as a workdir because the non-root user won't be able to
# create the tox working filesystem state in the source checkout because it is
# owned by root.
sudo --set-home -u nobody /tmp/tests/bin/tox -c /tmp/project/tox.ini --workdir /tmp -e ${TAHOE_LAFS_TOX_ENVIRONMENT}
