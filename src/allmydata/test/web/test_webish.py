"""
Tests for ``allmydata.webish``.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from uuid import (
    uuid4,
)
from errno import (
    EACCES,
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

from twisted.python.runtime import (
    platform,
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

        site = TahoeLAFSSite(self.mktemp(), Resource(), logPath=logPath)
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

        :param bytes tempdir: The temporary directory to give to the site.

        :return TahoeLAFSRequest: The new request instance.
        """
        site = TahoeLAFSSite(tempdir.path, Resource(), logPath=self.mktemp())
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
        request = self._create_request(tempdir)
        request.gotLength(request_body_size)
        self.assertThat(
            request.content,
            IsInstance(BytesIO),
        )

    def _large_request_test(self, request_body_size):
        """
        Assert that when a request with a body of of the given size is received
        its content is written to the directory the ``TahoeLAFSSite`` is
        configured with.
        """
        tempdir = FilePath(self.mktemp())
        tempdir.makedirs()
        request = self._create_request(tempdir)

        # So.  Bad news.  The temporary file for the uploaded content is
        # unnamed (and this isn't even necessarily a bad thing since it is how
        # you get automatic on-process-exit cleanup behavior on POSIX).  It's
        # not visible by inspecting the filesystem.  It has no name we can
        # discover.  Then how do we verify it is written to the right place?
        # The question itself is meaningless if we try to be too precise.  It
        # *has* no filesystem location.  However, it is still stored *on* some
        # filesystem.  We still want to make sure it is on the filesystem we
        # specified because otherwise it might be on a filesystem that's too
        # small or undesirable in some other way.
        #
        # I don't know of any way to ask a file descriptor which filesystem
        # it's on, either, though.  It might be the case that the [f]statvfs()
        # result could be compared somehow to infer the filesystem but
        # ... it's not clear what the failure modes might be there, across
        # different filesystems and runtime environments.
        #
        # Another approach is to make the temp directory unwriteable and
        # observe the failure when an attempt is made to create a file there.
        # This is hardly a lovely solution but at least it's kind of simple.
        #
        # It would be nice if it worked consistently cross-platform but on
        # Windows os.chmod is more or less broken.
        if platform.isWindows():
            request.gotLength(request_body_size)
            self.assertThat(
                tempdir.children(),
                HasLength(1),
            )
        else:
            tempdir.chmod(0o550)
            with self.assertRaises(OSError) as ctx:
                request.gotLength(request_body_size)
                raise Exception(
                    "OSError not raised, instead tempdir.children() = {}".format(
                        tempdir.children(),
                    ),
                )

            self.assertThat(
                ctx.exception.errno,
                Equals(EACCES),
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
