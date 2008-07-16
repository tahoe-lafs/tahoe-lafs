
from nevow import rend, inevow, tags as T
from allmydata.web.common import getxmlfile, get_arg

class CheckerResults(rend.Page):
    docFactory = getxmlfile("checker-results.xhtml")

    def __init__(self, results):
        self.r = results

    def render_storage_index(self, ctx, data):
        return self.r.get_storage_index_string()

    def render_mutability(self, ctx, data):
        return self.r.get_mutability_string()

    def render_results(self, ctx, data):
        return ctx.tag[self.r.to_string()]

    def render_return(self, ctx, data):
        req = inevow.IRequest(ctx)
        return_to = get_arg(req, "return_to", None)
        if return_to:
            return T.div[T.a(href=return_to)["Return to parent directory"]]
        return ""
