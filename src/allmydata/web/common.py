
import time
import json
from functools import wraps

from twisted.web import (
    http,
    resource,
    server,
)
from twisted.python import log
from nevow import appserver
from nevow.inevow import IRequest
from allmydata.interfaces import (
    MDMF_VERSION,
    SDMF_VERSION,
)
from allmydata.util.hashutil import timing_safe_compare
from allmydata.util.time_format import (
    format_delta,
    format_time,
)
from allmydata.util.encodingutil import (
    to_bytes,
)

# Originally part of this module, so still part of its API:
from .common_py3 import (  # noqa: F401
    get_arg, abbreviate_time, MultiFormatResource, WebError, humanize_exception,
    SlotsSequenceElement, render_exception, get_root, exception_to_child
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


def humanize_failure(f):
    """
    Create an human-oriented description of a failure along with some HTTP
    metadata.

    :param Failure f: The failure to describe.

    :return (bytes, int): A tuple of some prose and an HTTP code describing
        the failure.
    """
    return humanize_exception(f.value)


class MyExceptionHandler(appserver.DefaultExceptionHandler, object):
    def simple(self, ctx, text, code=http.BAD_REQUEST):
        req = IRequest(ctx)
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
            req = IRequest(ctx)
            method = req.method
            return self.simple(ctx,
                               "I don't know how to treat a %s request." % method,
                               http.NOT_IMPLEMENTED)
        req = IRequest(ctx)
        accept = req.getHeader("accept")
        if not accept:
            accept = "*/*"
        if "*/*" in accept or "text/*" in accept or "text/html" in accept:
            super = appserver.DefaultExceptionHandler
            return super.renderHTTP_exception(self, ctx, f)
        # use plain text
        traceback = f.getTraceback()
        return self.simple(ctx, traceback, http.INTERNAL_SERVER_ERROR)


class NeedOperationHandleError(WebError):
    pass




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

