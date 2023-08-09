"""
Tests for ``allmydata.webish``.
"""

import tempfile
from uuid import (
    uuid4,
)
from io import (
    BytesIO,
)

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    integers,
)

from testtools.matchers import (
    AfterPreprocessing,
    Contains,
    Equals,
    MatchesAll,
    Not,
    IsInstance,
    HasLength,
)

from twisted.python.filepath import (
    FilePath,
)
from twisted.web.test.requesthelper import (
    DummyChannel,
)
from twisted.web.resource import (
    Resource,
)

from ..common import (
    SyncTestCase,
)

from ...webish import (
    TahoeLAFSRequest,
    TahoeLAFSSite,
    anonymous_tempfile_factory,
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

    def test_form_fields_if_filename_set(self):
        """
        When a ``POST`` request is received, form fields are parsed into
        ``TahoeLAFSRequest.fields`` and the body is bytes (presuming ``filename``
        is set).
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
            {b"content-type": b"multipart/form-data; boundary=" + bytes(boundary, 'ascii')},
            form_data.encode("ascii"),
            AfterPreprocessing(
                lambda fs: {
                    k: fs.getvalue(k)
                    for k
                    in fs.keys()
                },
                Equals({
                    "foo": "bar",
                    "baz": b"some file contents",
                }),
            ),
        )

    def test_form_fields_if_name_is_file(self):
        """
        When a ``POST`` request is received, form fields are parsed into
        ``TahoeLAFSRequest.fields`` and the body is bytes when ``name``
        is set to ``"file"``.
        """
        form_data, boundary = multipart_formdata([
            [param(u"name", u"foo"),
             body(u"bar"),
            ],
            [param(u"name", u"file"),
             body(u"some file contents"),
            ],
        ])
        self._fields_test(
            b"POST",
            {b"content-type": b"multipart/form-data; boundary=" + bytes(boundary, 'ascii')},
            form_data.encode("ascii"),
            AfterPreprocessing(
                lambda fs: {
                    k: fs.getvalue(k)
                    for k
                    in fs.keys()
                },
                Equals({
                    "foo": "bar",
                    "file": b"some file contents",
                }),
            ),
        )

    def test_form_fields_require_correct_mime_type(self):
        """
        The body of a ``POST`` is not parsed into fields if its mime type is
        not ``multipart/form-data``.

        Reproducer for https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3854
        """
        data = u'{"lalala": "lolo"}'
        data = data.encode("utf-8")
        self._fields_test(b"POST", {"content-type": "application/json"},
                          data, Equals(None))


class TahoeLAFSSiteTests(SyncTestCase):
    """
    Tests for ``TahoeLAFSSite``.
    """
    def _test_censoring(self, path, censored):
        """
        Verify that the event logged for a request for ``path`` does not include
        ``path`` but instead includes ``censored``.

        :param bytes path: A request path.

        :param bytes censored: A replacement value for the request path in the
            access log.

        :return: ``None`` if the logging looks good.
        """
        logPath = self.mktemp()
        tempdir = self.mktemp()
        FilePath(tempdir).makedirs()

        site = TahoeLAFSSite(
            anonymous_tempfile_factory(tempdir),
            Resource(),
            logPath=logPath,
        )
        site.startFactory()

        channel = DummyChannel()
        channel.factory = site
        request = TahoeLAFSRequest(channel)

        request.gotLength(None)
        request.requestReceived(b"GET", path, b"HTTP/1.1")

        self.assertThat(
            FilePath(logPath).getContent(),
            MatchesAll(
                Contains(censored),
                Not(Contains(path)),
            ),
        )

    def test_private_key_censoring(self):
        """
        The log event for a request including a **private-key** query
        argument has the private key value censored.
        """
        self._test_censoring(
            b"/uri?uri=URI:CHK:aaa:bbb&private-key=AAAAaaaabbbb==",
            b"/uri?uri=[CENSORED]&private-key=[CENSORED]",
        )

    def test_uri_censoring(self):
        """
        The log event for a request for **/uri/<CAP>** has the capability value
        censored.
        """
        self._test_censoring(
            b"/uri/URI:CHK:aaa:bbb",
            b"/uri/[CENSORED]",
        )

    def test_file_censoring(self):
        """
        The log event for a request for **/file/<CAP>** has the capability value
        censored.
        """
        self._test_censoring(
            b"/file/URI:CHK:aaa:bbb",
            b"/file/[CENSORED]",
        )

    def test_named_censoring(self):
        """
        The log event for a request for **/named/<CAP>** has the capability value
        censored.
        """
        self._test_censoring(
            b"/named/URI:CHK:aaa:bbb",
            b"/named/[CENSORED]",
        )

    def test_uri_queryarg_censoring(self):
        """
        The log event for a request for **/uri?cap=<CAP>** has the capability
        value censored.
        """
        self._test_censoring(
            b"/uri?uri=URI:CHK:aaa:bbb",
            b"/uri?uri=[CENSORED]",
        )

    def _create_request(self, tempdir):
        """
        Create and return a new ``TahoeLAFSRequest`` hooked up to a
        ``TahoeLAFSSite``.

        :param FilePath tempdir: The temporary directory to configure the site
            to write large temporary request bodies to.  The temporary files
            will be named for ease of testing.

        :return TahoeLAFSRequest: The new request instance.
        """
        site = TahoeLAFSSite(
            lambda: tempfile.NamedTemporaryFile(dir=tempdir.path),
            Resource(),
            logPath=self.mktemp(),
        )
        site.startFactory()

        channel = DummyChannel()
        channel.site = site
        request = TahoeLAFSRequest(channel)
        return request

    @given(integers(min_value=0, max_value=1024 * 1024 - 1))
    def test_small_content(self, request_body_size):
        """
        A request body smaller than 1 MiB is kept in memory.
        """
        tempdir = FilePath(self.mktemp())
        tempdir.makedirs()
        request = self._create_request(tempdir)
        request.gotLength(request_body_size)
        self.assertThat(
            request.content,
            IsInstance(BytesIO),
        )

    def _large_request_test(self, request_body_size):
        """
        Assert that when a request with a body of the given size is
        received its content is written a temporary file created by the given
        tempfile factory.
        """
        tempdir = FilePath(self.mktemp())
        tempdir.makedirs()
        request = self._create_request(tempdir)
        request.gotLength(request_body_size)
        # We can see the temporary file in the temporary directory we
        # specified because _create_request makes a request that uses named
        # temporary files instead of the usual anonymous temporary files.
        self.assertThat(
            tempdir.children(),
            HasLength(1),
        )

    def test_unknown_request_size(self):
        """
        A request body with an unknown size is written to a file in the temporary
        directory passed to ``TahoeLAFSSite``.
        """
        self._large_request_test(None)

    @given(integers(min_value=1024 * 1024))
    def test_large_request(self, request_body_size):
        """
        A request body of 1 MiB or more is written to a file in the temporary
        directory passed to ``TahoeLAFSSite``.
        """
        self._large_request_test(request_body_size)


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
