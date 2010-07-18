
import os
from cStringIO import StringIO
import urllib
from allmydata.scripts.common_http import do_http, format_http_success, format_http_error
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path, \
                                     UnknownAliasError
from allmydata.util.encodingutil import quote_output

def put(options):
    """
    @param verbosity: 0, 1, or 2, meaning quiet, verbose, or very verbose

    @return: a Deferred which eventually fires with the exit code
    """
    nodeurl = options['node-url']
    aliases = options.aliases
    from_file = options.from_file
    to_file = options.to_file
    mutable = options['mutable']
    if options['quiet']:
        verbosity = 0
    else:
        verbosity = 2
    stdin = options.stdin
    stdout = options.stdout
    stderr = options.stderr

    if nodeurl[-1] != "/":
        nodeurl += "/"
    if to_file:
        # several possibilities for the TO_FILE argument.
        #  <none> : unlinked upload
        #  foo : TAHOE_ALIAS/foo
        #  subdir/foo : TAHOE_ALIAS/subdir/foo
        #  /oops/subdir/foo : DISALLOWED
        #  ALIAS:foo  : aliases[ALIAS]/foo
        #  ALIAS:subdir/foo  : aliases[ALIAS]/subdir/foo

        #  ALIAS:/oops/subdir/foo : DISALLOWED
        #  DIRCAP:./foo        : DIRCAP/foo
        #  DIRCAP:./subdir/foo : DIRCAP/subdir/foo
        #  MUTABLE-FILE-WRITECAP : filecap

        # FIXME: this shouldn't rely on a particular prefix.
        if to_file.startswith("URI:SSK:"):
            url = nodeurl + "uri/%s" % urllib.quote(to_file)
        else:
            try:
                rootcap, path = get_alias(aliases, to_file, DEFAULT_ALIAS)
            except UnknownAliasError, e:
                e.display(stderr)
                return 1
            if path.startswith("/"):
                suggestion = to_file.replace(u"/", u"", 1)
                print >>stderr, "Error: The remote filename must not start with a slash"
                print >>stderr, "Please try again, perhaps with %s" % quote_output(suggestion)
                return 1
            url = nodeurl + "uri/%s/" % urllib.quote(rootcap)
            if path:
                url += escape_path(path)
    else:
        # unlinked upload
        url = nodeurl + "uri"
    if mutable:
        url += "?mutable=true"
    if from_file:
        infileobj = open(os.path.expanduser(from_file), "rb")
    else:
        # do_http() can't use stdin directly: for one thing, we need a
        # Content-Length field. So we currently must copy it.
        if verbosity > 0:
            print >>stderr, "waiting for file data on stdin.."
        data = stdin.read()
        infileobj = StringIO(data)

    resp = do_http("PUT", url, infileobj)

    if resp.status in (200, 201,):
        print >>stderr, format_http_success(resp)
        print >>stdout, quote_output(resp.read(), quotemarks=False)
        return 0

    print >>stderr, format_http_error("Error", resp)
    return 1
