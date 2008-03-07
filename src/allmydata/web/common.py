
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

def abbreviate_time(data):
    # 1.23s, 790ms, 132us
    if data is None:
        return ""
    s = float(data)
    if s >= 1.0:
        return "%.2fs" % s
    if s >= 0.01:
        return "%dms" % (1000*s)
    if s >= 0.001:
        return "%.1fms" % (1000*s)
    return "%dus" % (1000000*s)

def abbreviate_rate(data):
    # 21.8kBps, 554.4kBps 4.37MBps
    if data is None:
        return ""
    r = float(data)
    if r > 1000000:
        return "%1.2fMBps" % (r/1000000)
    if r > 1000:
        return "%.1fkBps" % (r/1000)
    return "%dBps" % r

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
    return "%dB" % r
