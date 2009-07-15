
import urllib, simplejson
from twisted.protocols.basic import LineOnlyReceiver
from allmydata.util.abbreviate import abbreviate_space_both
from allmydata.scripts.slow_operation import SlowOperationRunner
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path
from allmydata.scripts.common_http import do_http

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
        rootcap, path = get_alias(options.aliases, where, DEFAULT_ALIAS)
        if path == '/':
            path = ''
        url = nodeurl + "uri/%s" % urllib.quote(rootcap)
        if path:
            url += "/" + escape_path(path)
        # todo: should it end with a slash?
        url += "?t=stream-manifest"
        resp = do_http("POST", url)
        if resp.status not in (200, 302):
            print >>stderr, "ERROR", resp.status, resp.reason, resp.read()
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
            print >>stderr, line
            return
        if line.startswith("ERROR:"):
            self.in_error = True
            self.rc = 1
            print >>stderr, line
            return

        d = simplejson.loads(line)
        if d["type"] in ("file", "directory"):
            if self.options["storage-index"]:
                si = d["storage-index"]
                if si:
                    print >>stdout, si
            elif self.options["verify-cap"]:
                vc = d["verifycap"]
                if vc:
                    print >>stdout, vc
            elif self.options["repair-cap"]:
                vc = d["repaircap"]
                if vc:
                    print >>stdout, vc
            else:
                try:
                    print >>stdout, d["cap"], "/".join(d["path"])
                except UnicodeEncodeError:
                    print >>stdout, d["cap"], "/".join([p.encode("utf-8")
                                                        for p in d["path"]])

def manifest(options):
    return ManifestStreamer().run(options)

class StatsGrabber(SlowOperationRunner):

    def make_url(self, base, ophandle):
        return base + "?t=start-deep-stats&ophandle=" + ophandle

    def write_results(self, data):
        stdout = self.options.stdout
        stderr = self.options.stderr
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
