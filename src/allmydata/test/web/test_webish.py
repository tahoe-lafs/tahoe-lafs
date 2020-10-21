"""
Tests for ``allmydata.webish``.
"""

from io import (
    BytesIO,
)
from uuid import (
    uuid4,
)

from testtools.matchers import (
    AfterPreprocessing,
    Equals,
)

from twisted.python.filepath import (
    FilePath,
)
from twisted.web.test.requesthelper import (
    DummyChannel,
)
from twisted.web.client import (
    FileBodyProducer,
)
from twisted.internet.task import (
    Cooperator,
)

from treq.multipart import (
    MultiPartProducer,
)

from ..common import (
    SyncTestCase,
)

from ...webish import (
    TahoeLAFSRequest,
)


class TahoeLAFSRequestTests(SyncTestCase):
    """
    Tests for ``TahoeLAFSRequest``.
    """
    def _fields_test(self, method, request_headers, request_body, match_fields):
        channel = DummyChannel()
        request = TahoeLAFSRequest(
            channel,
        )
        for (k, v) in request_headers.items():
            request.requestHeaders.setRawHeaders(k, [v])
        request.gotLength(len(request_body))
        request.handleContentChunk(request_body)
        request.requestReceived(method, b"/", b"HTTP/1.1")

        # We don't really care what happened to the request.  What we do care
        # about is what the `fields` attribute is set to.
        self.assertThat(
            request.fields,
            match_fields,
        )

    def test_no_form_fields(self):
        """
        When a ``GET`` request is received, ``TahoeLAFSRequest.fields`` is None.
        """
        self._fields_test(b"GET", {}, b"", Equals(None))

    def test_form_fields(self):
        """
        When a ``POST`` request is received, form fields are parsed into
        ``TahoeLAFSRequest.fields``.
        """
        form_data, boundary = multipart_formdata([
            [param(u"name", u"foo"),
             body(u"bar"),
            ],
            [param(u"name", u"baz"),
             param(u"filename", u"quux"),
             body(u"some file contents"),
            ],
        ])
        self._fields_test(
            b"POST",
            {b"content-type": b"multipart/form-data; boundary={}".format(boundary)},
            form_data.encode("ascii"),
            AfterPreprocessing(
                lambda fs: {
                    k: fs.getvalue(k)
                    for k
                    in fs.keys()
                },
                Equals({
                    b"foo": b"bar",
                    b"baz": b"some file contents",
                }),
            ),
        )


def param(name, value):
    return u"; {}={}".format(name, value)


def body(value):
    return u"\r\n\r\n{}".format(value)


def _field(field):
    yield u"Content-Disposition: form-data"
    for param in field:
        yield param


def _multipart_formdata(fields):
    for field in fields:
        yield u"".join(_field(field)) + u"\r\n"


def multipart_formdata(fields):
    """
    Serialize some simple fields into a multipart/form-data string.

    :param fields: A list of lists of unicode strings to assemble into the
        result.  See ``param`` and ``body``.

    :return unicode: The given fields combined into a multipart/form-data
        string.
    """
    boundary = str(uuid4())
    parts = list(_multipart_formdata(fields))
    parts.insert(0, u"")
    return (
        (u"--" + boundary + u"\r\n").join(parts),
        boundary,
    )
