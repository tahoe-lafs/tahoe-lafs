
from nevow import rend, inevow, tags as T
from allmydata.web.common import getxmlfile, get_arg
from allmydata.interfaces import ICheckerResults, IDeepCheckResults

class CheckerResults(rend.Page):
    docFactory = getxmlfile("checker-results.xhtml")

    def __init__(self, results):
        assert ICheckerResults(results)
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

class DeepCheckResults(rend.Page):
    docFactory = getxmlfile("deep-check-results.xhtml")

    def __init__(self, results):
        assert IDeepCheckResults(results)
        self.r = results

    def render_root_storage_index(self, ctx, data):
        return self.r.get_root_storage_index_string()

    def data_objects_checked(self, ctx, data):
        return self.r.count_objects_checked()
    def data_objects_healthy(self, ctx, data):
        return self.r.count_objects_healthy()
    def data_repairs_attempted(self, ctx, data):
        return self.r.count_repairs_attempted()
    def data_repairs_successful(self, ctx, data):
        return self.r.count_repairs_successful()

    def data_problems(self, ctx, data):
        for cr in self.r.get_problems():
            yield cr

    def render_return(self, ctx, data):
        req = inevow.IRequest(ctx)
        return_to = get_arg(req, "return_to", None)
        if return_to:
            return T.div[T.a(href=return_to)["Return to parent directory"]]
        return ""
