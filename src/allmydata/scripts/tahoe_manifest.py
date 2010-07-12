
import urllib, simplejson
from twisted.protocols.basic import LineOnlyReceiver
from allmydata.util.abbreviate import abbreviate_space_both
from allmydata.scripts.slow_operation import SlowOperationRunner
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path, \
                                     UnknownAliasError
from allmydata.scripts.common_http import do_http, format_http_error
from allmydata.util.encodingutil import quote_output, quote_path

class FakeTransport:
    disconnecting = False

class ManifestStreamer(LineOnlyReceiver):
    delimiter = "\n"

    def __init__(self):
        self.transport = FakeTransport()

    def run(self, options):
        self.rc = 0
        stdout = options.stdout
        stderr = options.stderr
        self.options = options
        nodeurl = options['node-url']
        if not nodeurl.endswith("/"):
            nodeurl += "/"
        self.nodeurl = nodeurl
        where = options.where
        try:
            rootcap, path = get_alias(options.aliases, where, DEFAULT_ALIAS)
        except UnknownAliasError, e:
            e.display(stderr)
            return 1
        if path == '/':
            path = ''
        url = nodeurl + "uri/%s" % urllib.quote(rootcap)
        if path:
            url += "/" + escape_path(path)
        # todo: should it end with a slash?
        url += "?t=stream-manifest"
        resp = do_http("POST", url)
        if resp.status not in (200, 302):
            print >>stderr, format_http_error("ERROR", resp)
            return 1
        #print "RESP", dir(resp)
        # use Twisted to split this into lines
        self.in_error = False
        while True:
            chunk = resp.read(100)
            if not chunk:
                break
            if self.options["raw"]:
                stdout.write(chunk)
            else:
                self.dataReceived(chunk)
        return self.rc

    def lineReceived(self, line):
        stdout = self.options.stdout
        stderr = self.options.stderr
        if self.in_error:
            print >>stderr, quote_output(line, quotemarks=False)
            return
        if line.startswith("ERROR:"):
            self.in_error = True
            self.rc = 1
            print >>stderr, quote_output(line, quotemarks=False)
            return

        try:
            d = simplejson.loads(line.decode('utf-8'))
        except Exception, e:
            print >>stderr, "ERROR could not decode/parse %s\nERROR  %r" % (quote_output(line), e)
        else:
            if d["type"] in ("file", "directory"):
                if self.options["storage-index"]:
                    si = d.get("storage-index", None)
                    if si:
                        print >>stdout, quote_output(si, quotemarks=False)
                elif self.options["verify-cap"]:
                    vc = d.get("verifycap", None)
                    if vc:
                        print >>stdout, quote_output(vc, quotemarks=False)
                elif self.options["repair-cap"]:
                    vc = d.get("repaircap", None)
                    if vc:
                        print >>stdout, quote_output(vc, quotemarks=False)
                else:
                    print >>stdout, "%s %s" % (quote_output(d["cap"], quotemarks=False),
                                               quote_path(d["path"], quotemarks=False))

def manifest(options):
    return ManifestStreamer().run(options)

class StatsGrabber(SlowOperationRunner):

    def make_url(self, base, ophandle):
        return base + "?t=start-deep-stats&ophandle=" + ophandle

    def write_results(self, data):
        stdout = self.options.stdout
        keys = ("count-immutable-files",
                "count-mutable-files",
                "count-literal-files",
                "count-files",
                "count-directories",
                "size-immutable-files",
                "size-mutable-files",
                "size-literal-files",
                "size-directories",
                "largest-directory",
                "largest-immutable-file",
                )
        width = max([len(k) for k in keys])
        print >>stdout, "Counts and Total Sizes:"
        for k in keys:
            fmt = "%" + str(width) + "s: %d"
            if k in data:
                value = data[k]
                if not k.startswith("count-") and value > 1000:
                    absize = abbreviate_space_both(value)
                    print >>stdout, fmt % (k, data[k]), "  ", absize
                else:
                    print >>stdout, fmt % (k, data[k])
        if data["size-files-histogram"]:
            print >>stdout, "Size Histogram:"
            prevmax = None
            maxlen = max([len(str(maxsize))
                          for (minsize, maxsize, count)
                          in data["size-files-histogram"]])
            maxcountlen = max([len(str(count))
                               for (minsize, maxsize, count)
                               in data["size-files-histogram"]])
            minfmt = "%" + str(maxlen) + "d"
            maxfmt = "%-" + str(maxlen) + "d"
            countfmt = "%-" + str(maxcountlen) + "d"
            linefmt = minfmt + "-" + maxfmt + " : " + countfmt + "    %s"
            for (minsize, maxsize, count) in data["size-files-histogram"]:
                if prevmax is not None and minsize != prevmax+1:
                    print >>stdout, " "*(maxlen-1) + "..."
                prevmax = maxsize
                print >>stdout, linefmt % (minsize, maxsize, count,
                                           abbreviate_space_both(maxsize))

def stats(options):
    return StatsGrabber().run(options)
