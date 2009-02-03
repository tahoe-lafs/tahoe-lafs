
from pprint import pprint
import urllib
import simplejson
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path
from allmydata.scripts.common_http import do_http
from allmydata.scripts.slow_operation import SlowOperationRunner

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
        pprint(data, stream=stdout)
    else:
        # make this prettier
        pprint(data, stream=stdout)
    return 0


class DeepChecker(SlowOperationRunner):

    def make_url(self, base, ophandle):
        url = base + "?t=start-deep-check&ophandle=" + ophandle
        if self.options["verify"]:
            url += "&verify=true"
        if self.options["repair"]:
            url += "&repair=true"
        return url

    def write_results(self, data):
        out = self.options.stdout
        err = self.options.stderr
        if self.options["repair"]:
            # todo: make this prettier
            pprint(data, stream=out)
        else:
            print >>out, "Objects Checked: %d" % data["count-objects-checked"]
            print >>out, "Objects Healthy: %d" % data["count-objects-healthy"]
            print >>out, "Objects Unhealthy: %d" % data["count-objects-unhealthy"]
            print >>out
            if data["list-unhealthy-files"]:
                print "Unhealthy Files:"
                for (pathname, cr) in data["list-unhealthy-files"]:
                    if pathname:
                        path_s = "/".join(pathname)
                    else:
                        path_s = "<root>"
                    print >>out, path_s, ":", cr["summary"]


def deepcheck(options):
    return DeepChecker().run(options)

