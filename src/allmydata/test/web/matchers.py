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
