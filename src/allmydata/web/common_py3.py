"""
Common utilities that are available from Python 3.

Can eventually be merged back into allmydata.web.common.
"""

from future.utils import PY2

from functools import wraps

if PY2:
    from nevow.inevow import IRequest as INevowRequest
else:
    INevowRequest = None

from twisted.web import resource, http, template
from twisted.web.template import tags as T
from twisted.web.iweb import IRequest

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
)
from allmydata.mutable.common import UnrecoverableFileError
from allmydata.util import abbreviate
from allmydata.util.encodingutil import quote_output


class WebError(Exception):
    def __init__(self, text, code=http.BAD_REQUEST):
        self.text = text
        self.code = code


def get_arg(ctx_or_req, argname, default=None, multiple=False):
    """Extract an argument from either the query args (req.args) or the form
    body fields (req.fields). If multiple=False, this returns a single value
    (or the default, which defaults to None), and the query args take
    precedence. If multiple=True, this returns a tuple of arguments (possibly
    empty), starting with all those in the query args.
    """
    results = []
    if PY2:
        req = INevowRequest(ctx_or_req)
        if argname in req.args:
            results.extend(req.args[argname])
        if req.fields and argname in req.fields:
            results.append(req.fields[argname].value)
    else:
        req = IRequest(ctx_or_req)
        if argname in req.args:
            results.extend(req.args[argname])
    if multiple:
        return tuple(results)
    if results:
        return results[0]
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
    formatDefault = None

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
        renderer = self._get_renderer(t)
        return renderer(req)

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
    # 1.23s, 790ms, 132us
    if data is None:
        return ""
    s = float(data)
    if s >= 10:
        return abbreviate.abbreviate_time(data)
    if s >= 1.0:
        return "%.2fs" % s
    if s >= 0.01:
        return "%.0fms" % (1000*s)
    if s >= 0.001:
        return "%.1fms" % (1000*s)
    return "%.0fus" % (1000000*s)


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


def get_root(ctx_or_req):
    if PY2:
        req = INevowRequest(ctx_or_req)
    else:
        req = IRequest(ctx_or_req)
    depth = len(req.prepath) + len(req.postpath)
    link = "/".join([".."] * depth)
    return link


class ReloadMixin(object):
    REFRESH_TIME = 1*60

    @template.renderer
    def refresh(self, req, tag):
        if self.monitor.is_finished():
            return ""
        # dreid suggests ctx.tag(**dict([("http-equiv", "refresh")]))
        # but I can't tell if he's joking or not
        tag.attributes["http-equiv"] = "refresh"
        tag.attributes["content"] = str(self.REFRESH_TIME)
        return tag

    @template.renderer
    def reload(self, req, tag):
        if self.monitor.is_finished():
            return ""
        # url.gethere would break a proxy, so the correct thing to do is
        # req.path[-1] + queryargs
        ophandle = req.prepath[-1]
        reload_target = ophandle + "?output=html"
        cancel_target = ophandle + "?t=cancel"
        cancel_button = T.form(T.input(type="submit", value="Cancel"),
                               action=cancel_target,
                               method="POST",
                               enctype="multipart/form-data",)

        return (T.h2("Operation still running: ",
                     T.a("Reload", href=reload_target),
                     ),
                cancel_button,)


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
