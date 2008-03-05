
from zope.interface import Interface
from nevow import loaders
from nevow.util import resource_filename

class IClient(Interface):
    pass


def getxmlfile(name):
    return loaders.xmlfile(resource_filename('allmydata.web', '%s' % name))

def boolean_of_arg(arg):
    assert arg.lower() in ("true", "t", "1", "false", "f", "0", "on", "off")
    return arg.lower() in ("true", "t", "1", "on")

def get_arg(req, argname, default=None, multiple=False):
    """Extract an argument from either the query args (req.args) or the form
    body fields (req.fields). If multiple=False, this returns a single value
    (or the default, which defaults to None), and the query args take
    precedence. If multiple=True, this returns a tuple of arguments (possibly
    empty), starting with all those in the query args.
    """
    results = []
    if argname in req.args:
        results.extend(req.args[argname])
    if req.fields and argname in req.fields:
        results.append(req.fields[argname].value)
    if multiple:
        return tuple(results)
    if results:
        return results[0]
    return default
