
import urllib, time
import simplejson
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path
from allmydata.scripts.common_http import do_http

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
    resp = do_http("GET", url)
    if resp.status == 404:
        print >>stderr, "No such file or directory"
        return 2
    if resp.status != 200:
        print >>stderr, "Error during GET: %s %s %s" % (resp.status,
                                                        resp.reason,
                                                        resp.read())
    data = resp.read()

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

    # we build up a series of rows, then we loop through them to compute a
    # maxwidth so we can format them tightly. Size, filename, and URI are the
    # variable-width ones.
    rows = []

    for name in childnames:
        child = children[name]
        childtype = child[0]
        ctime = child[1]["metadata"].get("ctime")
        mtime = child[1]["metadata"].get("mtime")
        rw_uri = child[1].get("rw_uri")
        ro_uri = child[1].get("ro_uri")
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
            t0 = "d"
            size = "-"
            classify = "/"
        elif childtype == "filenode":
            t0 = "-"
            size = str(child[1]['size'])
            classify = ""
            if rw_uri:
                classify = "*"
        else:
            t0 = "?"
            size = "?"
            classify = "?"
        t1 = "-"
        if ro_uri:
            t1 = "r"
        t2 = "-"
        if rw_uri:
            t2 = "w"
        t3 = "-"
        if childtype == "dirnode":
            t3 = "x"

        uri = rw_uri or ro_uri

        line = []
        if config["long"]:
            line.append(t0+t1+t2+t3)
            line.append(size)
            line.append(ctime_s)
        if not config["classify"]:
            classify = ""
        line.append(name + classify)
        if config["uri"]:
            line.append(uri)
        if config["readonly-uri"]:
            line.append(ro_uri or "-")

        rows.append(line)

    max_widths = []
    left_justifys = []
    for row in rows:
        for i,cell in enumerate(row):
            while len(max_widths) <= i:
                max_widths.append(0)
            while len(left_justifys) <= i:
                left_justifys.append(False)
            max_widths[i] = max(max_widths[i], len(cell))
            if cell.startswith("URI"):
                left_justifys[i] = True
    if len(left_justifys) == 1:
        left_justifys[0] = True
    fmt_pieces = []
    for i in range(len(max_widths)):
        piece = "%"
        if left_justifys[i]:
            piece += "-"
        piece += str(max_widths[i])
        piece += "s"
        fmt_pieces.append(piece)
    fmt = " ".join(fmt_pieces)
    for row in rows:
        print >>stdout, (fmt % tuple(row)).rstrip()
