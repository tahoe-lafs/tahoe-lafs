
import urllib
import simplejson
from twisted.protocols.basic import LineOnlyReceiver
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path
from allmydata.scripts.common_http import do_http

class Checker:
    pass

def check(options):
    stdout = options.stdout
    stderr = options.stderr
    nodeurl = options['node-url']
    if not nodeurl.endswith("/"):
        nodeurl += "/"
    where = options.where
    rootcap, path = get_alias(options.aliases, where, DEFAULT_ALIAS)
    if path == '/':
        path = ''
    url = nodeurl + "uri/%s" % urllib.quote(rootcap)
    if path:
        url += "/" + escape_path(path)
    # todo: should it end with a slash?
    url += "?t=check&output=JSON"
    if options["verify"]:
        url += "&verify=true"
    if options["repair"]:
        url += "&repair=true"

    resp = do_http("POST", url)
    if resp.status != 200:
        print >>stderr, "ERROR", resp.status, resp.reason, resp.read()
        return 1
    jdata = resp.read()
    if options.get("raw"):
        stdout.write(jdata)
        stdout.write("\n")
        return 0
    data = simplejson.loads(jdata)

    if options["repair"]:
        # show repair status
        if data["pre-repair-results"]["results"]["healthy"]:
            summary = "healthy"
        else:
            summary = "not healthy"
        stdout.write("Summary: %s\n" % summary)
        cr = data["pre-repair-results"]["results"]
        stdout.write(" storage index: %s\n" % data["storage-index"])
        stdout.write(" good-shares: %d (encoding is %d-of-%d)\n"
                     % (cr["count-shares-good"],
                        cr["count-shares-needed"],
                        cr["count-shares-expected"]))
        stdout.write(" wrong-shares: %d\n" % cr["count-wrong-shares"])
        corrupt = cr["list-corrupt-shares"]
        if corrupt:
            stdout.write(" corrupt shares:\n")
            for (serverid, storage_index, sharenum) in corrupt:
                stdout.write("  server %s, SI %s, shnum %d\n" %
                             (serverid, storage_index, sharenum))
        if data["repair-attempted"]:
            if data["repair-successful"]:
                stdout.write(" repair successful\n")
            else:
                stdout.write(" repair failed\n")
    else:
        stdout.write("Summary: %s\n" % data["summary"])
        cr = data["results"]
        stdout.write(" storage index: %s\n" % data["storage-index"])
        stdout.write(" good-shares: %d (encoding is %d-of-%d)\n"
                     % (cr["count-shares-good"],
                        cr["count-shares-needed"],
                        cr["count-shares-expected"]))
        stdout.write(" wrong-shares: %d\n" % cr["count-wrong-shares"])
        corrupt = cr["list-corrupt-shares"]
        if corrupt:
            stdout.write(" corrupt shares:\n")
            for (serverid, storage_index, sharenum) in corrupt:
                stdout.write("  server %s, SI %s, shnum %d\n" %
                             (serverid, storage_index, sharenum))
    return 0


class FakeTransport:
    disconnecting = False

class DeepCheckOutput(LineOnlyReceiver):
    delimiter = "\n"
    def __init__(self, options):
        self.transport = FakeTransport()

        self.verbose = bool(options["verbose"])
        self.stdout = options.stdout
        self.num_objects = 0
        self.files_healthy = 0
        self.files_unhealthy = 0

    def lineReceived(self, line):
        d = simplejson.loads(line)
        stdout = self.stdout
        if d["type"] not in ("file", "directory"):
            return
        self.num_objects += 1
        # non-verbose means print a progress marker every 100 files
        if self.num_objects % 100 == 0:
            print >>stdout, "%d objects checked.." % self.num_objects
        cr = d["check-results"]
        if cr["results"]["healthy"]:
            self.files_healthy += 1
        else:
            self.files_unhealthy += 1
        if self.verbose:
            # verbose means also print one line per file
            path = d["path"]
            if not path:
                path = ["<root>"]
            summary = cr.get("summary", "Healthy (LIT)")
            try:
                print >>stdout, "%s: %s" % ("/".join(path), summary)
            except UnicodeEncodeError:
                print >>stdout, "%s: %s" % ("/".join([p.encode("utf-8")
                                                      for p in path]),
                                            summary)
        # always print out corrupt shares
        for shareloc in cr["results"].get("list-corrupt-shares", []):
            (serverid, storage_index, sharenum) = shareloc
            print >>stdout, " corrupt: server %s, SI %s, shnum %d" % \
                  (serverid, storage_index, sharenum)

    def done(self):
        stdout = self.stdout
        print >>stdout, "done: %d objects checked, %d healthy, %d unhealthy" \
              % (self.num_objects, self.files_healthy, self.files_unhealthy)

class DeepCheckAndRepairOutput(LineOnlyReceiver):
    delimiter = "\n"
    def __init__(self, options):
        self.transport = FakeTransport()

        self.verbose = bool(options["verbose"])
        self.stdout = options.stdout
        self.num_objects = 0
        self.pre_repair_files_healthy = 0
        self.pre_repair_files_unhealthy = 0
        self.repairs_attempted = 0
        self.repairs_successful = 0
        self.post_repair_files_healthy = 0
        self.post_repair_files_unhealthy = 0

    def lineReceived(self, line):
        d = simplejson.loads(line)
        stdout = self.stdout
        if d["type"] not in ("file", "directory"):
            return
        self.num_objects += 1
        # non-verbose means print a progress marker every 100 files
        if self.num_objects % 100 == 0:
            print >>stdout, "%d objects checked.." % self.num_objects
        crr = d["check-and-repair-results"]
        if d["storage-index"]:
            if crr["pre-repair-results"]["results"]["healthy"]:
                was_healthy = True
                self.pre_repair_files_healthy += 1
            else:
                was_healthy = False
                self.pre_repair_files_unhealthy += 1
            if crr["post-repair-results"]["results"]["healthy"]:
                self.post_repair_files_healthy += 1
            else:
                self.post_repair_files_unhealthy += 1
        else:
            # LIT file
            was_healthy = True
            self.pre_repair_files_healthy += 1
            self.post_repair_files_healthy += 1
        if crr["repair-attempted"]:
            self.repairs_attempted += 1
            if crr["repair-successful"]:
                self.repairs_successful += 1
        if self.verbose:
            # verbose means also print one line per file
            path = d["path"]
            if not path:
                path = ["<root>"]
            # we don't seem to have a summary available, so build one
            if was_healthy:
                summary = "healthy"
            else:
                summary = "not healthy"
            try:
                print >>stdout, "%s: %s" % ("/".join(path), summary)
            except UnicodeEncodeError:
                print >>stdout, "%s: %s" % ("/".join([p.encode("utf-8")
                                                      for p in path]),
                                            summary)
        # always print out corrupt shares
        prr = crr.get("pre-repair-results", {})
        for shareloc in prr.get("results", {}).get("list-corrupt-shares", []):
            (serverid, storage_index, sharenum) = shareloc
            print >>stdout, " corrupt: server %s, SI %s, shnum %d" % \
                  (serverid, storage_index, sharenum)

        # always print out repairs
        if crr["repair-attempted"]:
            if crr["repair-successful"]:
                print >>stdout, " repair successful"
            else:
                print >>stdout, " repair failed"

    def done(self):
        stdout = self.stdout
        print >>stdout, "done: %d objects checked" % self.num_objects
        print >>stdout, " pre-repair: %d healthy, %d unhealthy" \
              % (self.pre_repair_files_healthy,
                 self.pre_repair_files_unhealthy)
        print >>stdout, " %d repairs attempted, %d successful, %d failed" \
              % (self.repairs_attempted,
                 self.repairs_successful,
                 (self.repairs_attempted - self.repairs_successful))
        print >>stdout, " post-repair: %d healthy, %d unhealthy" \
              % (self.post_repair_files_healthy,
                 self.post_repair_files_unhealthy)

class DeepCheckStreamer(LineOnlyReceiver):

    def run(self, options):
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
        url += "?t=stream-deep-check"
        if options["verify"]:
            url += "&verify=true"
        if options["repair"]:
            url += "&repair=true"
            output = DeepCheckAndRepairOutput(options)
        else:
            output = DeepCheckOutput(options)
        resp = do_http("POST", url)
        if resp.status not in (200, 302):
            print >>stderr, "ERROR", resp.status, resp.reason, resp.read()
            return 1

        # use Twisted to split this into lines
        while True:
            chunk = resp.read(100)
            if not chunk:
                break
            if self.options["raw"]:
                stdout.write(chunk)
            else:
                output.dataReceived(chunk)
        if not self.options["raw"]:
            output.done()
        return 0

def deepcheck(options):
    return DeepCheckStreamer().run(options)
