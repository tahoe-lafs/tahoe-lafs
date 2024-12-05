"""
Ported to Python 3.
"""
from __future__ import annotations

from six import ensure_str
from importlib.resources import files as resource_files
from importlib.resources import as_file
from contextlib import ExitStack
import weakref
from typing import Optional, Union, TypeVar, overload
from typing_extensions import Literal

import time
import json
from functools import wraps
from base64 import urlsafe_b64decode

from hyperlink import (
    DecodedURL,
)

from eliot import (
    Message,
    start_action,
)
from eliot.twisted import (
    DeferredContext,
)

from twisted.web import (
    http,
    resource,
    template,
    static,
)
from twisted.web.iweb import (
    IRequest,
)
from twisted.web.template import (
    tags,
)
from twisted.web.server import (
    NOT_DONE_YET,
)
from twisted.web.util import (
    DeferredResource,
    FailureElement,
    redirectTo,
)
from twisted.python.reflect import (
    fullyQualifiedName,
)
from twisted.python import log
from twisted.python.failure import (
    Failure,
)
from twisted.internet.defer import (
    CancelledError,
    maybeDeferred,
)
from twisted.web.resource import (
    IResource,
)

from allmydata.dirnode import ONLY_FILES, _OnlyFiles
from allmydata import blacklist
from allmydata.interfaces import (
    EmptyPathnameComponentError,
    ExistingChildError,
    FileTooLargeError,
    MustBeDeepImmutableError,
    MustBeReadonlyError,
    MustNotBeUnknownRWError,
    NoSharesError,
    NoSuchChildError,
    NotEnoughSharesError,
    MDMF_VERSION,
    SDMF_VERSION,
)
from allmydata.mutable.common import UnrecoverableFileError
from allmydata.util.time_format import (
    format_delta,
    format_time,
)
from allmydata.util.encodingutil import (
    quote_output,
    quote_output_u,
    to_bytes,
)
from allmydata.util import abbreviate
from allmydata.crypto.rsa import PrivateKey, PublicKey, create_signing_keypair_from_string


class WebError(Exception):
    def __init__(self, text, code=http.BAD_REQUEST):
        self.text = text
        self.code = code


def get_filenode_metadata(filenode):
    metadata = {'mutable': filenode.is_mutable()}
    if metadata['mutable']:
        mutable_type = filenode.get_version()
        assert mutable_type in (SDMF_VERSION, MDMF_VERSION)
        if mutable_type == MDMF_VERSION:
            file_format = "MDMF"
        else:
            file_format = "SDMF"
    else:
        file_format = "CHK"
    metadata['format'] = file_format
    size = filenode.get_size()
    if size is not None:
        metadata['size'] = size
    return metadata

def boolean_of_arg(arg):  # type: (bytes) -> bool
    assert isinstance(arg, bytes)
    if arg.lower() not in (b"true", b"t", b"1", b"false", b"f", b"0", b"on", b"off"):
        raise WebError("invalid boolean argument: %r" % (arg,), http.BAD_REQUEST)
    return arg.lower() in (b"true", b"t", b"1", b"on")


def parse_replace_arg(replace: bytes) -> Union[bool,_OnlyFiles]:
    assert isinstance(replace, bytes)
    if replace.lower() == b"only-files":
        return ONLY_FILES
    try:
        return boolean_of_arg(replace)
    except WebError:
        raise WebError("invalid replace= argument: %r" % (ensure_str(replace),), http.BAD_REQUEST)


def get_format(req, default="CHK"):
    arg = get_arg(req, "format", None)
    if not arg:
        if boolean_of_arg(get_arg(req, "mutable", "false")):
            return "SDMF"
        return default
    if arg.upper() == b"CHK":
        return "CHK"
    elif arg.upper() == b"SDMF":
        return "SDMF"
    elif arg.upper() == b"MDMF":
        return "MDMF"
    else:
        raise WebError("Unknown format: %s, I know CHK, SDMF, MDMF" % str(arg, "ascii"),
                       http.BAD_REQUEST)

def get_mutable_type(file_format): # accepts result of get_format()
    if file_format == "SDMF":
        return SDMF_VERSION
    elif file_format == "MDMF":
        return MDMF_VERSION
    else:
        # this is also used to identify which formats are mutable. Use
        #  if get_mutable_type(file_format) is not None:
        #      do_mutable()
        #  else:
        #      do_immutable()
        return None


def parse_offset_arg(offset):  # type: (bytes) -> Union[int,None]
    # XXX: This will raise a ValueError when invoked on something that
    # is not an integer. Is that okay? Or do we want a better error
    # message? Since this call is going to be used by programmers and
    # their tools rather than users (through the wui), it is not
    # inconsistent to return that, I guess.
    if offset is not None:
        return int(offset)

    return offset


def get_root(req):  # type: (IRequest) -> str
    """
    Get a relative path with parent directory segments that refers to the root
    location known to the given request.  This seems a lot like the constant
    absolute path **/** but it will behave differently if the Tahoe-LAFS HTTP
    server is reverse-proxied and mounted somewhere other than at the root.

    :param twisted.web.iweb.IRequest req: The request to consider.

    :return: A string like ``../../..`` with the correct number of segments to
        reach the root.
    """
    if not IRequest.providedBy(req):
        raise TypeError(
            "get_root requires IRequest provider, got {!r}".format(req),
        )
    depth = len(req.prepath) + len(req.postpath)
    link = "/".join([".."] * depth)
    return link


def convert_children_json(nodemaker, children_json):
    """I convert the JSON output of GET?t=json into the dict-of-nodes input
    to both dirnode.create_subdirectory() and
    client.create_directory(initial_children=). This is used by
    t=mkdir-with-children and t=mkdir-immutable"""
    children = {}
    if children_json:
        data = json.loads(children_json)
        for (namex, (ctype, propdict)) in list(data.items()):
            namex = str(namex)
            writecap = to_bytes(propdict.get("rw_uri"))
            readcap = to_bytes(propdict.get("ro_uri"))
            metadata = propdict.get("metadata", {})
            # name= argument is just for error reporting
            childnode = nodemaker.create_from_cap(writecap, readcap, name=namex)
            children[namex] = (childnode, metadata)
    return children


def compute_rate(bytes, seconds):
    if bytes is None:
      return None

    if seconds is None or seconds == 0:
      return None

    # negative values don't make sense here
    assert bytes > -1
    assert seconds > 0

    return bytes / seconds


def abbreviate_rate(data):
    """
    Convert number of bytes/second into human readable strings (unicode).

    Uses metric measures, so 1000 not 1024, e.g. 21.8kBps, 554.4kBps, 4.37MBps.

    :param data: Either ``None`` or integer.

    :return: Unicode string.
    """
    if data is None:
        return u""
    r = float(data)
    if r > 1000000:
        return u"%1.2fMBps" % (r/1000000)
    if r > 1000:
        return u"%.1fkBps" % (r/1000)
    return u"%.0fBps" % r


def abbreviate_size(data):
    """
    Convert number of bytes into human readable strings (unicode).

    Uses metric measures, so 1000 not 1024, e.g. 21.8kB, 554.4kB, 4.37MB.

    :param data: Either ``None`` or integer.

    :return: Unicode string.
    """
    if data is None:
        return u""
    r = float(data)
    if r > 1000000000:
        return u"%1.2fGB" % (r/1000000000)
    if r > 1000000:
        return u"%1.2fMB" % (r/1000000)
    if r > 1000:
        return u"%.1fkB" % (r/1000)
    return u"%.0fB" % r

def plural(sequence_or_length):
    if isinstance(sequence_or_length, int):
        length = sequence_or_length
    else:
        length = len(sequence_or_length)
    if length == 1:
        return ""
    return "s"

def text_plain(text, req):
    req.setHeader("content-type", "text/plain")
    req.setHeader("content-length", b"%d" % len(text))
    return text

def spaces_to_nbsp(text):
    return str(text).replace(u' ', u'\u00A0')

def render_time_delta(time_1, time_2):
    return spaces_to_nbsp(format_delta(time_1, time_2))

def render_time(t):
    return spaces_to_nbsp(format_time(time.localtime(t)))

def render_time_attr(t):
    return format_time(time.localtime(t))


# XXX: to make UnsupportedMethod return 501 NOT_IMPLEMENTED instead of 500
# Internal Server Error, we either need to do that ICanHandleException trick,
# or make sure that childFactory returns a WebErrorResource (and never an
# actual exception). The latter is growing increasingly annoying.

def should_create_intermediate_directories(req):
    t = str(get_arg(req, "t", "").strip(), "ascii")
    return bool(req.method in (b"PUT", b"POST") and
                t not in ("delete", "rename", "rename-form", "check"))

def humanize_exception(exc):
    """
    Like ``humanize_failure`` but for an exception.

    :param Exception exc: The exception to describe.

    :return: See ``humanize_failure``.
    """
    if isinstance(exc, EmptyPathnameComponentError):
        return ("The webapi does not allow empty pathname components, "
                "i.e. a double slash", http.BAD_REQUEST)
    if isinstance(exc, ExistingChildError):
        return ("There was already a child by that name, and you asked me "
                "to not replace it.", http.CONFLICT)
    if isinstance(exc, NoSuchChildError):
        quoted_name = quote_output_u(exc.args[0], quotemarks=False)
        return ("No such child: %s" % quoted_name, http.NOT_FOUND)
    if isinstance(exc, NotEnoughSharesError):
        t = ("NotEnoughSharesError: This indicates that some "
             "servers were unavailable, or that shares have been "
             "lost to server departure, hard drive failure, or disk "
             "corruption. You should perform a filecheck on "
             "this object to learn more.\n\nThe full error message is:\n"
             "%s") % str(exc)
        return (t, http.GONE)
    if isinstance(exc, NoSharesError):
        t = ("NoSharesError: no shares could be found. "
             "Zero shares usually indicates a corrupt URI, or that "
             "no servers were connected, but it might also indicate "
             "severe corruption. You should perform a filecheck on "
             "this object to learn more.\n\nThe full error message is:\n"
             "%s") % str(exc)
        return (t, http.GONE)
    if isinstance(exc, UnrecoverableFileError):
        t = ("UnrecoverableFileError: the directory (or mutable file) could "
             "not be retrieved, because there were insufficient good shares. "
             "This might indicate that no servers were connected, "
             "insufficient servers were connected, the URI was corrupt, or "
             "that shares have been lost due to server departure, hard drive "
             "failure, or disk corruption. You should perform a filecheck on "
             "this object to learn more.")
        return (t, http.GONE)
    if isinstance(exc, MustNotBeUnknownRWError):
        quoted_name = quote_output(exc.args[1], encoding="utf-8")
        immutable = exc.args[2]
        if immutable:
            t = ("MustNotBeUnknownRWError: an operation to add a child named "
                 "%s to a directory was given an unknown cap in a write slot.\n"
                 "If the cap is actually an immutable readcap, then using a "
                 "webapi server that supports a later version of Tahoe may help.\n\n"
                 "If you are using the webapi directly, then specifying an immutable "
                 "readcap in the read slot (ro_uri) of the JSON PROPDICT, and "
                 "omitting the write slot (rw_uri), would also work in this "
                 "case.") % quoted_name
        else:
            t = ("MustNotBeUnknownRWError: an operation to add a child named "
                 "%s to a directory was given an unknown cap in a write slot.\n"
                 "Using a webapi server that supports a later version of Tahoe "
                 "may help.\n\n"
                 "If you are using the webapi directly, specifying a readcap in "
                 "the read slot (ro_uri) of the JSON PROPDICT, as well as a "
                 "writecap in the write slot if desired, would also work in this "
                 "case.") % quoted_name
        return (t, http.BAD_REQUEST)
    if isinstance(exc, MustBeDeepImmutableError):
        quoted_name = quote_output(exc.args[1], encoding="utf-8")
        t = ("MustBeDeepImmutableError: a cap passed to this operation for "
             "the child named %s, needed to be immutable but was not. Either "
             "the cap is being added to an immutable directory, or it was "
             "originally retrieved from an immutable directory as an unknown "
             "cap.") % quoted_name
        return (t, http.BAD_REQUEST)
    if isinstance(exc, MustBeReadonlyError):
        quoted_name = quote_output(exc.args[1], encoding="utf-8")
        t = ("MustBeReadonlyError: a cap passed to this operation for "
             "the child named '%s', needed to be read-only but was not. "
             "The cap is being passed in a read slot (ro_uri), or was retrieved "
             "from a read slot as an unknown cap.") % quoted_name
        return (t, http.BAD_REQUEST)
    if isinstance(exc, blacklist.FileProhibited):
        t = "Access Prohibited: %s" % quote_output(exc.reason, encoding="utf-8", quotemarks=False)
        return (t, http.FORBIDDEN)
    if isinstance(exc, WebError):
        return (exc.text, exc.code)
    if isinstance(exc, FileTooLargeError):
        return ("FileTooLargeError: %s" % (exc,), http.REQUEST_ENTITY_TOO_LARGE)
    return (str(exc), None)


def humanize_failure(f):
    """
    Create an human-oriented description of a failure along with some HTTP
    metadata.

    :param Failure f: The failure to describe.

    :return (bytes, int): A tuple of some prose and an HTTP code describing
        the failure.
    """
    return humanize_exception(f.value)


class NeedOperationHandleError(WebError):
    pass


class SlotsSequenceElement(template.Element):
    """
    ``SlotsSequenceElement` is a minimal port of Nevow's sequence renderer for
    twisted.web.template.

    Tags passed in to be templated will have two renderers available: ``item``
    and ``tag``.
    """

    def __init__(self, tag, seq):
        self.loader = template.TagLoader(tag)
        self.seq = seq

    @template.renderer
    def header(self, request, tag):
        return tag

    @template.renderer
    def item(self, request, tag):
        """
        A template renderer for each sequence item.

        ``tag`` will be cloned for each item in the sequence provided, and its
        slots filled from the sequence item. Each item must be dict-like enough
        for ``tag.fillSlots(**item)``. Each cloned tag will be siblings with no
        separator beween them.
        """
        for item in self.seq:
            yield tag.clone(deep=False).fillSlots(**item)

    @template.renderer
    def empty(self, request, tag):
        """
        A template renderer for empty sequences.

        This renderer will either return ``tag`` unmodified if the provided
        sequence has no items, or return the empty string if there are any
        items.
        """
        if len(self.seq) > 0:
            return u''
        else:
            return tag


def exception_to_child(getChild):
    """
    Decorate ``getChild`` method with exception handling behavior to render an
    error page reflecting the exception.
    """
    @wraps(getChild)
    def g(self, name, req):
        # Bind the method to the instance so it has a better
        # fullyQualifiedName later on.  This is not necessary on Python 3.
        bound_getChild = getChild.__get__(self, type(self))

        action = start_action(
            action_type=u"allmydata:web:common-getChild",
            uri=req.uri,
            method=req.method,
            name=name,
            handler=fullyQualifiedName(bound_getChild),
        )
        with action.context():
            result = DeferredContext(maybeDeferred(bound_getChild, name, req))
            result.addCallbacks(
                _getChild_done,
                _getChild_failed,
                callbackArgs=(self,),
            )
            result = result.addActionFinish()
        return DeferredResource(result)
    return g


def _getChild_done(child, parent):
    Message.log(
        message_type=u"allmydata:web:common-getChild:result",
        result=fullyQualifiedName(type(child)),
    )
    if child is None:
        return resource.NoResource()
    return child


def _getChild_failed(reason):
    text, code = humanize_failure(reason)
    return resource.ErrorPage(code, "Error", text)


def render_exception(render):
    """
    Decorate a ``render_*`` method with exception handling behavior to render
    an error page reflecting the exception.
    """
    @wraps(render)
    def g(self, request):
        # Bind the method to the instance so it has a better
        # fullyQualifiedName later on.  This is not necessary on Python 3.
        bound_render = render.__get__(self, type(self))

        action = start_action(
            action_type=u"allmydata:web:common-render",
            uri=request.uri,
            method=request.method,
            handler=fullyQualifiedName(bound_render),
        )
        if getattr(request, "dont_apply_extra_processing", False):
            with action:
                return bound_render(request)

        with action.context():
            result = DeferredContext(maybeDeferred(bound_render, request))
            # Apply `_finish` all of our result handling logic to whatever it
            # returned.
            result.addBoth(_finish, bound_render, request)
            d = result.addActionFinish()

        # If the connection is lost then there's no point running our _finish
        # logic because it has nowhere to send anything.  There may also be no
        # point in finishing whatever operation was being performed because
        # the client cannot be informed of its result.  Also, Twisted Web
        # raises exceptions from some Request methods if they're used after
        # the connection is lost.
        request.notifyFinish().addErrback(
            lambda ignored: d.cancel(),
        )
        return NOT_DONE_YET

    return g


def _finish(result, render, request):
    """
    Try to finish rendering the response to a request.

    This implements extra convenience functionality not provided by Twisted
    Web.  Various resources in Tahoe-LAFS made use of this functionality when
    it was provided by Nevow.  Rather than making that application code do the
    more tedious thing itself, we duplicate the functionality here.

    :param result: Something returned by a render method which we can turn
        into a response.

    :param render: The original render method which produced the result.

    :param request: The request being responded to.

    :return: ``None``
    """
    if isinstance(result, Failure):
        if result.check(CancelledError):
            return
        Message.log(
            message_type=u"allmydata:web:common-render:failure",
            message=result.getErrorMessage(),
        )
        _finish(
            _renderHTTP_exception(request, result),
            render,
            request,
        )
    elif IResource.providedBy(result):
        # If result is also using @render_exception then we don't want to
        # double-apply the logic.  This leads to an attempt to double-finish
        # the request.  If it isn't using @render_exception then you should
        # fix it so it is.
        Message.log(
            message_type=u"allmydata:web:common-render:resource",
            resource=fullyQualifiedName(type(result)),
        )
        result.render(request)
    elif isinstance(result, str):
        Message.log(
            message_type=u"allmydata:web:common-render:unicode",
        )
        request.write(result.encode("utf-8"))
        request.finish()
    elif isinstance(result, bytes):
        Message.log(
            message_type=u"allmydata:web:common-render:bytes",
        )
        request.write(result)
        request.finish()
    elif isinstance(result, DecodedURL):
        Message.log(
            message_type=u"allmydata:web:common-render:DecodedURL",
        )
        _finish(redirectTo(result.to_text().encode("utf-8"), request), render, request)
    elif result is None:
        Message.log(
            message_type=u"allmydata:web:common-render:None",
        )
        request.finish()
    elif result == NOT_DONE_YET:
        Message.log(
            message_type=u"allmydata:web:common-render:NOT_DONE_YET",
        )
        pass
    else:
        Message.log(
            message_type=u"allmydata:web:common-render:unknown",
        )
        log.err("Request for {!r} handled by {!r} returned unusable {!r}".format(
            request.uri,
            fullyQualifiedName(render),
            result,
        ))
        request.setResponseCode(http.INTERNAL_SERVER_ERROR)
        _finish(b"Internal Server Error", render, request)


def _renderHTTP_exception(request, failure):
    try:
        text, code = humanize_failure(failure)
    except:
        log.msg("exception in humanize_failure")
        log.msg("argument was %s" % (failure,))
        log.err()
        text = str(failure)
        code = None

    if code is not None:
        return _renderHTTP_exception_simple(request, text, code)

    accept = request.getHeader("accept")
    if not accept:
        accept = "*/*"
    if "*/*" in accept or "text/*" in accept or "text/html" in accept:
        request.setResponseCode(http.INTERNAL_SERVER_ERROR)
        return template.renderElement(
            request,
            tags.html(
                tags.head(
                    tags.title(u"Exception"),
                ),
                tags.body(
                    FailureElement(failure),
                ),
            ),
        )

    # use plain text
    traceback = failure.getTraceback()
    return _renderHTTP_exception_simple(
        request,
        traceback,
        http.INTERNAL_SERVER_ERROR,
    )


def _renderHTTP_exception_simple(request, text, code):
    request.setResponseCode(code)
    request.setHeader("content-type", "text/plain;charset=utf-8")
    if isinstance(text, str):
        text = text.encode("utf-8")
    request.setHeader("content-length", b"%d" % len(text))
    return text


def handle_when_done(req, d):
    when_done = get_arg(req, "when_done", None)
    if when_done:
        d.addCallback(lambda res: DecodedURL.from_text(when_done.decode("utf-8")))
    return d


def url_for_string(req, url_string):
    """
    Construct a universal URL using the given URL string.

    :param IRequest req: The request being served.  If ``redir_to`` is not
        absolute then this is used to determine the net location of this
        server and the resulting URL is made to point at it.

    :param bytes url_string: A byte string giving a universal or absolute URL.

    :return DecodedURL: An absolute URL based on this server's net location
        and the given URL string.
    """
    url = DecodedURL.from_text(url_string.decode("utf-8"))
    if not url.host:
        root = req.URLPath()
        netloc = root.netloc.split(b":", 1)
        if len(netloc) == 1:
            host = netloc
            port = None
        else:
            host = netloc[0]
            port = int(netloc[1])
        url = url.replace(
            scheme=root.scheme.decode("ascii"),
            host=host.decode("ascii"),
            port=port,
        )
    return url

T = TypeVar("T")

@overload
def get_arg(req: IRequest, argname: str | bytes, default: Optional[T] = None, *, multiple: Literal[False] = False) -> T | bytes: ...

@overload
def get_arg(req: IRequest, argname: str | bytes, default: Optional[T] = None, *, multiple: Literal[True]) -> T | tuple[bytes, ...]: ...

def get_arg(req: IRequest, argname: str | bytes, default: Optional[T] = None, *, multiple: bool = False) -> None | T | bytes | tuple[bytes, ...]:
    """Extract an argument from either the query args (req.args) or the form
    body fields (req.fields). If multiple=False, this returns a single value
    (or the default, which defaults to None), and the query args take
    precedence. If multiple=True, this returns a tuple of arguments (possibly
    empty), starting with all those in the query args.

    :param TahoeLAFSRequest req: The request to consider.

    :return: Either bytes or tuple of bytes.
    """
    # Need to import here to prevent circular import:
    from ..webish import TahoeLAFSRequest

    if isinstance(argname, str):
        argname_bytes = argname.encode("utf-8")
    else:
        argname_bytes = argname

    results : list[bytes] = []
    if req.args is not None and argname_bytes in req.args:
        results.extend(req.args[argname_bytes])
    argname_unicode = str(argname_bytes, "utf-8")
    if isinstance(req, TahoeLAFSRequest) and req.fields and argname_unicode in req.fields:
        # In all but one or two unit tests, the request will be a
        # TahoeLAFSRequest.
        value = req.fields[argname_unicode].value
        if isinstance(value, str):
            value = value.encode("utf-8")
        results.append(value)
    if multiple:
        return tuple(results)
    if results:
        return results[0]

    if isinstance(default, str):
        return default.encode("utf-8")
    return default


class MultiFormatResource(resource.Resource, object):
    """
    ``MultiFormatResource`` is a ``resource.Resource`` that can be rendered in
    a number of different formats.

    Rendered format is controlled by a query argument (given by
    ``self.formatArgument``).  Different resources may support different
    formats but ``json`` is a pretty common one.  ``html`` is the default
    format if nothing else is given as the ``formatDefault``.
    """
    formatArgument = "t"
    formatDefault = None  # type: Optional[str]

    def render(self, req):
        """
        Dispatch to a renderer for a particular format, as selected by a query
        argument.

        A renderer for the format given by the query argument matching
        ``formatArgument`` will be selected and invoked.  render_HTML will be
        used as a default if no format is selected (either by query arguments
        or by ``formatDefault``).

        :return: The result of the selected renderer.
        """
        t = get_arg(req, self.formatArgument, self.formatDefault)
        # It's either bytes or None.
        if isinstance(t, bytes):
            t = str(t, "ascii")
        renderer = self._get_renderer(t)
        result = renderer(req)
        # On Python 3, json.dumps() returns Unicode for example, but
        # twisted.web expects bytes. Instead of updating every single render
        # method, just handle Unicode one time here.
        if isinstance(result, str):
            result = result.encode("utf-8")
        return result

    def _get_renderer(self, fmt):
        """
        Get the renderer for the indicated format.

        :param str fmt: The format.  If a method with a prefix of ``render_``
            and a suffix of this format (upper-cased) is found, it will be
            used.

        :return: A callable which takes a twisted.web Request and renders a
            response.
        """
        renderer = None

        if fmt is not None:
            try:
                renderer = getattr(self, "render_{}".format(fmt.upper()))
            except AttributeError:
                return resource.ErrorPage(
                    http.BAD_REQUEST,
                    "Bad Format",
                    "Unknown {} value: {!r}".format(self.formatArgument, fmt),
                ).render

        if renderer is None:
            renderer = self.render_HTML

        return renderer


def abbreviate_time(data):
    """
    Convert number of seconds into human readable string.

    :param data: Either ``None`` or integer or float, seconds.

    :return: Unicode string.
    """
    # 1.23s, 790ms, 132us
    if data is None:
        return u""
    s = float(data)
    if s >= 10:
        return abbreviate.abbreviate_time(data)
    if s >= 1.0:
        return u"%.2fs" % s
    if s >= 0.01:
        return u"%.0fms" % (1000*s)
    if s >= 0.001:
        return u"%.1fms" % (1000*s)
    return u"%.0fus" % (1000000*s)

def get_keypair(request: IRequest) -> tuple[PublicKey, PrivateKey] | None:
    """
    Load a keypair from a urlsafe-base64-encoded RSA private key in the
    **private-key** argument of the given request, if there is one.
    """
    privkey_der = get_arg(request, "private-key", default=None, multiple=False)
    if privkey_der is None:
        return None
    privkey, pubkey = create_signing_keypair_from_string(urlsafe_b64decode(privkey_der))
    return pubkey, privkey


def add_static_children(root: IResource):
    """
    Add static files from C{allmydata.web} to the given resource.

    Package resources may be on the filesystem, or they may be in a zip
    or something, so we need to do a bit more work to serve them as
    static files.
    """
    temporary_file_manager = ExitStack()
    static_dir = resource_files("allmydata.web") / "static"
    for child in static_dir.iterdir():
        child_path = child.name.encode("utf-8")
        root.putChild(child_path, static.File(
            str(temporary_file_manager.enter_context(as_file(child)))
        ))
    weakref.finalize(root, temporary_file_manager.close)
