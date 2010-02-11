
import os, time
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path, \
                                     UnknownAliasError
from allmydata.scripts.common_http import do_http
from allmydata.util import base32
import urllib
import simplejson

class SlowOperationRunner:

    def run(self, options):
        stderr = options.stderr
        self.options = options
        self.ophandle = ophandle = base32.b2a(os.urandom(16))
        nodeurl = options['node-url']
        if not nodeurl.endswith("/"):
            nodeurl += "/"
        self.nodeurl = nodeurl
        where = options.where
        try:
            rootcap, path = get_alias(options.aliases, where, DEFAULT_ALIAS)
        except UnknownAliasError, e:
            print >>stderr, "error: %s" % e.args[0]
            return 1
        if path == '/':
            path = ''
        url = nodeurl + "uri/%s" % urllib.quote(rootcap)
        if path:
            url += "/" + escape_path(path)
        # todo: should it end with a slash?
        url = self.make_url(url, ophandle)
        resp = do_http("POST", url)
        if resp.status not in (200, 302):
            print >>stderr, "ERROR", resp.status, resp.reason, resp.read()
            return 1
        # now we poll for results. We nominally poll at t=1, 5, 10, 30, 60,
        # 90, k*120 seconds, but if the poll takes non-zero time, that will
        # be slightly longer. I'm not worried about trying to make up for
        # that time.

        return self.wait_for_results()

    def poll_times(self):
        for i in (1,5,10,30,60,90):
            yield i
        i = 120
        while True:
            yield i
            i += 120

    def wait_for_results(self):
        last = 0
        for next in self.poll_times():
            delay = next - last
            time.sleep(delay)
            last = next
            if self.poll():
                return 0

    def poll(self):
        url = self.nodeurl + "operations/" + self.ophandle
        url += "?t=status&output=JSON&release-after-complete=true"
        stdout = self.options.stdout
        stderr = self.options.stderr
        resp = do_http("GET", url)
        if resp.status != 200:
            print >>stderr, "ERROR", resp.status, resp.reason, resp.read()
            return True
        jdata = resp.read()
        data = simplejson.loads(jdata)
        if not data["finished"]:
            return False
        if self.options.get("raw"):
            print >>stdout, jdata
            return True
        self.write_results(data)
        return True

