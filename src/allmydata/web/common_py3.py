"""
Common utilities that are available from Python 3.

Can eventually be merged back into allmydata.web.common.
"""

from future.utils import PY2

if PY2:
    from nevow.inevow import IRequest as INevowRequest
else:
    INevowRequest = None

from twisted.web import resource, http
from twisted.web.iweb import IRequest

from allmydata.util import abbreviate


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
