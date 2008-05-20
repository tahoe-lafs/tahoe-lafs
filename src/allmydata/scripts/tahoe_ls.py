
import urllib, time
import simplejson
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path

def list(nodeurl, aliases, where, config, stdout, stderr):
    if not nodeurl.endswith("/"):
        nodeurl += "/"
    if where.endswith("/"):
        where = where[:-1]
    rootcap, path = get_alias(aliases, where, DEFAULT_ALIAS)
    url = nodeurl + "uri/%s" % urllib.quote(rootcap)
    if path:
        # move where.endswith check here?
        url += "/" + escape_path(path)
    assert not url.endswith("/")
    url += "?t=json"
    data = urllib.urlopen(url).read()

    if config['json']:
        print >>stdout, data
        return

    parsed = simplejson.loads(data)
    nodetype, d = parsed
    children = {}
    if nodetype == "dirnode":
        children = d['children']
    elif nodetype == "filenode":
        childname = path.split("/")[-1]
        children = {childname: d}
    childnames = sorted(children.keys())
    now = time.time()
    for name in childnames:
        child = children[name]
        childtype = child[0]
        ctime = child[1]["metadata"].get("ctime")
        mtime = child[1]["metadata"].get("mtime")
        if ctime:
            # match for formatting that GNU 'ls' does
            if (now - ctime) > 6*30*24*60*60:
                # old files
                fmt = "%b %d  %Y"
            else:
                fmt = "%b %d %H:%M"
            ctime_s = time.strftime(fmt, time.localtime(ctime))
        else:
            ctime_s = "-"
        if childtype == "dirnode":
            t = "d---------"
            size = "-"
            classify = "/"
        elif childtype == "filenode":
            t = "----------"
            size = child[1]['size']
            classify = ""
            if "rw_uri" in child[1]:
                classify = "*" # mutable

        uri = child[1].get("rw_uri", child[1].get("ro_uri", "-"))

        line = []
        if config["long"]:
            line.append("%s %10s %12s" % (t, size, ctime_s))
        if config["uri"]:
            line.append(uri)
        line.append(name)
        if config["classify"]:
            line[-1] += classify

        print >>stdout, " ".join(line)
