
import urllib, time
import simplejson
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path, \
                                     UnknownAliasError
from allmydata.scripts.common_http import do_http, format_http_error
from allmydata.util.stringutils import unicode_to_output, quote_output, is_printable_ascii, to_str

def list(options):
    nodeurl = options['node-url']
    aliases = options.aliases
    where = options.where
    stdout = options.stdout
    stderr = options.stderr

    if not nodeurl.endswith("/"):
        nodeurl += "/"
    if where.endswith("/"):
        where = where[:-1]
    try:
        rootcap, path = get_alias(aliases, where, DEFAULT_ALIAS)
    except UnknownAliasError, e:
        e.display(stderr)
        return 1
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
        print >>stderr, format_http_error("Error during GET", resp)
        if resp.status == 0:
            return 3
        else:
            return resp.status

    data = resp.read()

    if options['json']:
        # The webapi server should always output printable ASCII.
        if is_printable_ascii(data):
            print >>stdout, data
            return 0
        else:
            print >>stderr, "The JSON response contained unprintable characters:\n%s" % quote_output(data)
            return 1

    try:
        parsed = simplejson.loads(data)
    except Exception, e:
        print >>stderr, "error: %s" % quote_output(e.args[0], quotemarks=False)
        print >>stderr, "Could not parse JSON response:\n%s" % quote_output(data)
        return 1

    nodetype, d = parsed
    children = {}
    if nodetype == "dirnode":
        children = d['children']
    else:
        # paths returned from get_alias are always valid UTF-8
        childname = path.split("/")[-1].decode('utf-8')
        children = {childname: (nodetype, d)}
        if "metadata" not in d:
            d["metadata"] = {}
    childnames = sorted(children.keys())
    now = time.time()

    # we build up a series of rows, then we loop through them to compute a
    # maxwidth so we can format them tightly. Size, filename, and URI are the
    # variable-width ones.
    rows = []
    has_unknowns = False

    for name in childnames:
        child = children[name]
        name = unicode(name)
        childtype = child[0]

        # See webapi.txt for a discussion of the meanings of unix local
        # filesystem mtime and ctime, Tahoe mtime and ctime, and Tahoe
        # linkmotime and linkcrtime.
        ctime = child[1].get("metadata", {}).get('tahoe', {}).get("linkcrtime")
        if not ctime:
            ctime = child[1]["metadata"].get("ctime")

        mtime = child[1].get("metadata", {}).get('tahoe', {}).get("linkmotime")
        if not mtime:
            mtime = child[1]["metadata"].get("mtime")
        rw_uri = to_str(child[1].get("rw_uri"))
        ro_uri = to_str(child[1].get("ro_uri"))
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
            size = str(child[1].get("size", "?"))
            classify = ""
            if rw_uri:
                classify = "*"
        else:
            has_unknowns = True
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
        if options["long"]:
            line.append(t0+t1+t2+t3)
            line.append(size)
            line.append(ctime_s)
        if not options["classify"]:
            classify = ""

        encoding_error = False
        try:
            line.append(unicode_to_output(name) + classify)
        except UnicodeEncodeError:
            encoding_error = True
            line.append(quote_output(name) + classify)

        if options["uri"]:
            line.append(uri)
        if options["readonly-uri"]:
            line.append(quote_output(ro_uri or "-", quotemarks=False))

        rows.append((encoding_error, line))

    max_widths = []
    left_justifys = []
    for (encoding_error, row) in rows:
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
    
    rc = 0
    for (encoding_error, row) in rows:
        if encoding_error:
            print >>stderr, (fmt % tuple(row)).rstrip()
            rc = 1
        else:
            print >>stdout, (fmt % tuple(row)).rstrip()

    if rc == 1:
        print >>stderr, "\nThis listing included files whose names could not be converted to the terminal" \
                        "\noutput encoding. Their names are shown using backslash escapes and in quotes."
    if has_unknowns:
        print >>stderr, "\nThis listing included unknown objects. Using a webapi server that supports" \
                        "\na later version of Tahoe may help."

    return rc
