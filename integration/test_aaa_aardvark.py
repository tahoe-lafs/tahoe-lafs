# So these dummy tests run first and instantiate the pre-requisites
# first (e.g. introducer) and therefore print "something" on the
# console as we go (a . or the test-name in "-v"/verbose mode)

# You can safely skip any of these tests, it'll just appear to "take
# longer" to start the first test as the fixtures get built

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401


def test_create_flogger(flog_gatherer):
    print("Created flog_gatherer")


def test_create_introducer(introducer):
    print("Created introducer")


def test_create_storage(storage_nodes):
    print("Created {} storage nodes".format(len(storage_nodes)))
