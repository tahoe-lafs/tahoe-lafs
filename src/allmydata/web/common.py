from future.utils import PY2
from past.builtins import unicode

import time
import json
from functools import wraps

from twisted.web import (
    http,
    resource,
    server,
    template,
)
from twisted.web.iweb import IRequest as ITwistedRequest
from twisted.python import log
if PY2:
    from nevow.appserver import DefaultExceptionHandler
    from nevow.inevow import IRequest as INevowRequest
else:
    class DefaultExceptionHandler:
        def __init__(self, *args, **kwargs):
            raise NotImplementedError("Still not ported to Python 3")
    INevowRequest = None

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
from allmydata.util.hashutil import timing_safe_compare
from allmydata.util.time_format import (
    format_delta,
    format_time,
)
from allmydata.util.encodingutil import (
    quote_output,
    to_bytes,
)

# Originally part of this module, so still part of its API:
from .common_py3 import (  # noqa: F401
    get_arg, abbreviate_time, MultiFormatResource, WebError,
)


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

def boolean_of_arg(arg):
    # TODO: ""
    if arg.lower() not in ("true", "t", "1", "false", "f", "0", "on", "off"):
        raise WebError("invalid boolean argument: %r" % (arg,), http.BAD_REQUEST)
    return arg.lower() in ("true", "t", "1", "on")

def parse_replace_arg(replace):
    if replace.lower() == "only-files":
        return replace
    try:
        return boolean_of_arg(replace)
    except WebError:
        raise WebError("invalid replace= argument: %r" % (replace,), http.BAD_REQUEST)


def get_format(req, default="CHK"):
    arg = get_arg(req, "format", None)
    if not arg:
        if boolean_of_arg(get_arg(req, "mutable", "false")):
            return "SDMF"
        return default
    if arg.upper() == "CHK":
        return "CHK"
    elif arg.upper() == "SDMF":
        return "SDMF"
    elif arg.upper() == "MDMF":
        return "MDMF"
    else:
        raise WebError("Unknown format: %s, I know CHK, SDMF, MDMF" % arg,
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


def parse_offset_arg(offset):
    # XXX: This will raise a ValueError when invoked on something that
    # is not an integer. Is that okay? Or do we want a better error
    # message? Since this call is going to be used by programmers and
    # their tools rather than users (through the wui), it is not
    # inconsistent to return that, I guess.
    if offset is not None:
        offset = int(offset)

    return offset


def get_root(ctx_or_req):
    if PY2:
        req = INevowRequest(ctx_or_req)
    else:
        req = ITwistedRequest(ctx_or_req)
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
        for (namex, (ctype, propdict)) in data.iteritems():
            namex = unicode(namex)
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

    return 1.0 * bytes / seconds

def abbreviate_rate(data):
    # 21.8kBps, 554.4kBps 4.37MBps
    if data is None:
        return ""
    r = float(data)
    if r > 1000000:
        return "%1.2fMBps" % (r/1000000)
    if r > 1000:
        return "%.1fkBps" % (r/1000)
    return "%.0fBps" % r

def abbreviate_size(data):
    # 21.8kB, 554.4kB 4.37MB
    if data is None:
        return ""
    r = float(data)
    if r > 1000000000:
        return "%1.2fGB" % (r/1000000000)
    if r > 1000000:
        return "%1.2fMB" % (r/1000000)
    if r > 1000:
        return "%.1fkB" % (r/1000)
    return "%.0fB" % r

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
    return unicode(text).replace(u' ', u'\u00A0')

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
    t = get_arg(req, "t", "").strip()
    return bool(req.method in ("PUT", "POST") and
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
        quoted_name = quote_output(exc.args[0], encoding="utf-8", quotemarks=False)
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


class MyExceptionHandler(DefaultExceptionHandler, object):
    def simple(self, ctx, text, code=http.BAD_REQUEST):
        req = INevowRequest(ctx)
        req.setResponseCode(code)
        #req.responseHeaders.setRawHeaders("content-encoding", [])
        #req.responseHeaders.setRawHeaders("content-disposition", [])
        req.setHeader("content-type", "text/plain;charset=utf-8")
        if isinstance(text, unicode):
            text = text.encode("utf-8")
        req.setHeader("content-length", b"%d" % len(text))
        req.write(text)
        # TODO: consider putting the requested URL here
        req.finishRequest(False)

    def renderHTTP_exception(self, ctx, f):
        try:
            text, code = humanize_failure(f)
        except:
            log.msg("exception in humanize_failure")
            log.msg("argument was %s" % (f,))
            log.err()
            text, code = str(f), None
        if code is not None:
            return self.simple(ctx, text, code)
        if f.check(server.UnsupportedMethod):
            # twisted.web.server.Request.render() has support for transforming
            # this into an appropriate 501 NOT_IMPLEMENTED or 405 NOT_ALLOWED
            # return code, but nevow does not.
            req = INevowRequest(ctx)
            method = req.method
            return self.simple(ctx,
                               "I don't know how to treat a %s request." % method,
                               http.NOT_IMPLEMENTED)
        req = INevowRequest(ctx)
        accept = req.getHeader("accept")
        if not accept:
            accept = "*/*"
        if "*/*" in accept or "text/*" in accept or "text/html" in accept:
            super = DefaultExceptionHandler
            return super.renderHTTP_exception(self, ctx, f)
        # use plain text
        traceback = f.getTraceback()
        return self.simple(ctx, traceback, http.INTERNAL_SERVER_ERROR)


class NeedOperationHandleError(WebError):
    pass


class SlotsSequenceElement(template.Element):
    """
    ``SlotsSequenceElement` is a minimal port of nevow's sequence renderer for
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


class TokenOnlyWebApi(resource.Resource, object):
    """
    I provide a rend.Page implementation that only accepts POST calls,
    and only if they have a 'token=' arg with the correct
    authentication token (see
    :meth:`allmydata.client.Client.get_auth_token`). Callers must also
    provide the "t=" argument to indicate the return-value (the only
    valid value for this is "json")

    Subclasses should override 'post_json' which should process the
    API call and return a string which encodes a valid JSON
    object. This will only be called if the correct token is present
    and valid (during renderHTTP processing).
    """

    def __init__(self, client):
        self.client = client

    def post_json(self, req):
        return NotImplemented

    def render(self, req):
        if req.method != 'POST':
            raise server.UnsupportedMethod(('POST',))
        if req.args.get('token', False):
            raise WebError("Do not pass 'token' as URL argument", http.BAD_REQUEST)
        # not using get_arg() here because we *don't* want the token
        # argument to work if you passed it as a GET-style argument
        token = None
        if req.fields and 'token' in req.fields:
            token = req.fields['token'].value.strip()
        if not token:
            raise WebError("Missing token", http.UNAUTHORIZED)
        if not timing_safe_compare(token, self.client.get_auth_token()):
            raise WebError("Invalid token", http.UNAUTHORIZED)

        t = get_arg(req, "t", "").strip()
        if not t:
            raise WebError("Must provide 't=' argument")
        if t == u'json':
            try:
                return self.post_json(req)
            except WebError as e:
                req.setResponseCode(e.code)
                return json.dumps({"error": e.text})
            except Exception as e:
                message, code = humanize_exception(e)
                req.setResponseCode(500 if code is None else code)
                return json.dumps({"error": message})
        else:
            raise WebError("'%s' invalid type for 't' arg" % (t,), http.BAD_REQUEST)


def exception_to_child(f):
    """
    Decorate ``getChild`` method with exception handling behavior to render an
    error page reflecting the exception.
    """
    @wraps(f)
    def g(self, name, req):
        try:
            return f(self, name, req)
        except Exception as e:
            description, status = humanize_exception(e)
            return resource.ErrorPage(status, "Error", description)
    return g


def render_exception(f):
    """
    Decorate a ``render_*`` method with exception handling behavior to render
    an error page reflecting the exception.
    """
    @wraps(f)
    def g(self, request):
        try:
            return f(self, request)
        except Exception as e:
            description, status = humanize_exception(e)
            return resource.ErrorPage(status, "Error", description).render(request)
    return g
