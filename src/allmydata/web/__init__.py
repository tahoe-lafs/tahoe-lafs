import simplejson

from twisted.web.server import UnsupportedMethod

from nevow import rend, url, tags as T
from nevow.inevow import IRequest

from allmydata.web.common import getxmlfile, get_arg, WebError


class TokenOnlyWebApi(rend.Page):
    """
    I provide a rend.Page implementation that only accepts POST calls,
    and only if they have a 'token=' arg with the correct
    authentication token (see
    :meth:`allmydata.client.Client.get_auth_token`). Callers must also
    provide the "t=" argument to indicate the return-value (the only
    valid value for this is "json")

    Subclasses should override '_render_json' which should process the
    API call and return a valid JSON object. This will only be called
    if the correct token is present and valid (during renderHTTP
    processing).
    """

    def __init__(self, client):
        super(MagicFolderWebApi, self).__init__(client)
        self.client = client

    def renderHTTP(self, ctx):
        req = IRequest(ctx)
        t = get_arg(req, "t", None)
        if req.method != 'POST':
            raise UnsupportedMethod(('POST',))

        token = get_arg(req, "token", None)
        # XXX need constant-time comparison?
        if token is None or token != self.client.get_auth_token():
            raise WebError("Missing or invalid token.", 400)

        if t is None:
            raise WebError("Must provide 't=' argument")

        t = t.strip()
        if t == 'json':
            return self._render_json(req)

        raise WebError("'%s' invalid type for 't' arg" % (t,), 400)


