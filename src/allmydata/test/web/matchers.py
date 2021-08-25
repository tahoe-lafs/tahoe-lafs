"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import attr

from testtools.matchers import Mismatch

@attr.s
class _HasResponseCode(object):
    match_expected_code = attr.ib()

    def match(self, response):
        actual_code = response.code
        mismatch = self.match_expected_code.match(actual_code)
        if mismatch is None:
            return None
        return Mismatch(
            u"Response {} code: {}".format(
                response,
                mismatch.describe(),
            ),
            mismatch.get_details(),
        )

def has_response_code(match_expected_code):
    """
    Match a Treq response with the given code.

    :param int expected_code: The HTTP response code expected of the response.
    """
    return _HasResponseCode(match_expected_code)
