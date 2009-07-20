
import re
import urllib
import simplejson
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path
from allmydata.scripts.common_http import do_http

# this script is used for both 'mv' and 'ln'

def mv(options, mode="move"):
    nodeurl = options['node-url']
    aliases = options.aliases
    from_file = options.from_file
    to_file = options.to_file
    stdout = options.stdout
    stderr = options.stderr

    if nodeurl[-1] != "/":
        nodeurl += "/"
    rootcap, from_path = get_alias(aliases, from_file, DEFAULT_ALIAS)
    from_url = nodeurl + "uri/%s" % urllib.quote(rootcap)
    if from_path:
        from_url += "/" + escape_path(from_path)
    # figure out the source cap
    data = urllib.urlopen(from_url + "?t=json").read()
    nodetype, attrs = simplejson.loads(data)
    cap = attrs.get("rw_uri") or attrs["ro_uri"]
    # simplejson sometimes returns unicode, but we know that it's really just
    # an ASCII file-cap.
    cap = str(cap)

    # now get the target
    rootcap, path = get_alias(aliases, to_file, DEFAULT_ALIAS)
    to_url = nodeurl + "uri/%s" % urllib.quote(rootcap)
    if path:
        to_url += "/" + escape_path(path)

    if to_url.endswith("/"):
        # "mv foo.txt bar/" == "mv foo.txt bar/foo.txt"
        to_url += escape_path(from_path[from_path.rfind("/")+1:])

    to_url += "?t=uri&replace=only-files"

    resp = do_http("PUT", to_url, cap)
    status = resp.status
    if not re.search(r'^2\d\d$', str(status)):
        if status == 409:
            print >>stderr, "Error: You can't overwrite a directory with a file"
        else:
            print >>stderr, "error, got %s %s" % (resp.status, resp.reason)
            print >>stderr, resp.read()
            if mode == "move":
                print >>stderr, "NOT removing the original"
        return

    if mode == "move":
        # now remove the original
        resp = do_http("DELETE", from_url)
        if not re.search(r'^2\d\d$', str(status)):
            print >>stderr, "error, got %s %s" % (resp.status, resp.reason)
            print >>stderr, resp.read()

    print >>stdout, "OK"
    return
