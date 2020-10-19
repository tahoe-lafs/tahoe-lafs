"""
Tests for ``allmydata.web.common``.
"""

from bs4 import (
    BeautifulSoup,
)
from hyperlink import (
    DecodedURL,
)

from testtools.matchers import (
    Equals,
    Contains,
    MatchesPredicate,
)
from testtools.twistedsupport import (
    succeeded,
    has_no_result,
)

from twisted.internet.defer import (
    fail,
)
from twisted.web.server import (
    NOT_DONE_YET,
)
from twisted.web.resource import (
    Resource,
)

from ...web.common import (
    render_exception,
)

from ..common import (
    SyncTestCase,
)
from ..common_web import (
    render,
)
from .common import (
    assert_soup_has_tag_with_attributes,
)

class StaticResource(Resource):
    def __init__(self, response):
        Resource.__init__(self)
        self._response = response

    @render_exception
    def render(self, request):
        return self._response


class RenderExceptionTests(SyncTestCase):
    """
    Tests for ``render_exception``.
    """
    def test_exception(self):
        """
        If the decorated method raises an exception then the exception is rendered
        into the response.
        """
        class R(Resource):
            @render_exception
            def render(self, request):
                raise Exception("synthetic exception")

        self.assertThat(
            render(R(), {}),
            succeeded(
                Contains(b"synthetic exception"),
            ),
        )

    def test_failure(self):
        """
        If the decorated method returns a ``Deferred`` that fires with a
        ``Failure`` then the exception the ``Failure`` wraps is rendered into
        the response.
        """
        resource = StaticResource(fail(Exception("synthetic exception")))
        self.assertThat(
            render(resource, {}),
            succeeded(
                Contains(b"synthetic exception"),
            ),
        )

    def test_resource(self):
        """
        If the decorated method returns an ``IResource`` provider then that
        resource is used to render the response.
        """
        resource = StaticResource(StaticResource(b"static result"))
        self.assertThat(
            render(resource, {}),
            succeeded(
                Equals(b"static result"),
            ),
        )

    def test_unicode(self):
        """
        If the decorated method returns a ``unicode`` string then that string is
        UTF-8 encoded and rendered into the response.
        """
        text = u"\N{SNOWMAN}"
        resource = StaticResource(text)
        self.assertThat(
            render(resource, {}),
            succeeded(
                Equals(text.encode("utf-8")),
            ),
        )

    def test_bytes(self):
        """
        If the decorated method returns a ``bytes`` string then that string is
        rendered into the response.
        """
        data = b"hello world"
        resource = StaticResource(data)
        self.assertThat(
            render(resource, {}),
            succeeded(
                Equals(data),
            ),
        )

    def test_decodedurl(self):
        """
        If the decorated method returns a ``DecodedURL`` then a redirect to that
        location is rendered into the response.
        """
        loc = u"http://example.invalid/foo?bar=baz"
        resource = StaticResource(DecodedURL.from_text(loc))
        self.assertThat(
            render(resource, {}),
            succeeded(
                MatchesPredicate(
                    lambda value: assert_soup_has_tag_with_attributes(
                        self,
                        BeautifulSoup(value),
                        "meta",
                        {"http-equiv": "refresh",
                         "content": "0;URL={}".format(loc.encode("ascii")),
                        },
                    )
                    # The assertion will raise if it has a problem, otherwise
                    # return None.  Turn the None into something
                    # MatchesPredicate recognizes as success.
                    or True,
                    "did not find meta refresh tag in %r",
                ),
            ),
        )

    def test_none(self):
        """
        If the decorated method returns ``None`` then the response is finished
        with no additional content.
        """
        self.assertThat(
            render(StaticResource(None), {}),
            succeeded(
                Equals(b""),
            ),
        )

    def test_not_done_yet(self):
        """
        If the decorated method returns ``NOT_DONE_YET`` then the resource is
        responsible for finishing the request itself.
        """
        the_request = []
        class R(Resource):
            @render_exception
            def render(self, request):
                the_request.append(request)
                return NOT_DONE_YET

        d = render(R(), {})

        self.assertThat(
            d,
            has_no_result(),
        )

        the_request[0].write(b"some content")
        the_request[0].finish()

        self.assertThat(
            d,
            succeeded(
                Equals(b"some content"),
            ),
        )

    def test_unknown(self):
        """
        If the decorated method returns something which is not explicitly
        supported, an internal server error is rendered into the response.
        """
        self.assertThat(
            render(StaticResource(object()), {}),
            succeeded(
                Equals(b"Internal Server Error"),
            ),
        )
