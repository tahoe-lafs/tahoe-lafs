#! /usr/bin/env python

import os, shutil, sys
from cStringIO import StringIO
from twisted.internet import defer, reactor, protocol, error
from twisted.application import service, internet
from twisted.web.client import getPage
from allmydata import client, introducer_and_vdrive
from allmydata.scripts import create_node
from allmydata.util import testutil
import foolscap
from foolscap import eventual
from twisted.python import log

class SystemFramework(testutil.PollMixin):
    numnodes = 5

    def __init__(self, basedir, mode):
        self.basedir = basedir = os.path.abspath(basedir)
        if not basedir.startswith(os.path.abspath(".")):
            raise AssertionError("safety issue: basedir must be a subdir")
        if os.path.exists(basedir):
            shutil.rmtree(basedir)
        os.mkdir(basedir)
        self.sparent = service.MultiService()
        self.sparent.startService()
        self.proc = None
        self.tub = foolscap.Tub()
        self.tub.setServiceParent(self.sparent)
        self.discard_shares = True
        self.mode = mode
        self.failed = False

    def run(self):
        log.startLogging(open(os.path.join(self.basedir, "log"), "w"),
                         setStdout=False)
        #logfile = open(os.path.join(self.basedir, "log"), "w")
        #flo = log.FileLogObserver(logfile)
        #log.startLoggingWithObserver(flo.emit, setStdout=False)
        d = eventual.fireEventually()
        d.addCallback(lambda res: self.setUp())
        d.addCallback(lambda res: self.do_test())
        d.addBoth(self.tearDown)
        def _err(err):
            self.failed = err
            log.err(err)
            print err
        d.addErrback(_err)
        def _done(res):
            reactor.stop()
            return res
        d.addBoth(_done)
        reactor.run()
        if self.failed:
            self.failed.raiseException()

    def setUp(self):
        #print "STARTING"
        self.stats = {}
        self.statsfile = open(os.path.join(self.basedir, "stats.out"), "w")
        d = self.make_introducer_and_vdrive()
        def _more(res):
            self.make_nodes()
            return self.start_client()
        d.addCallback(_more)
        def _record_control_furl(control_furl):
            self.control_furl = control_furl
            #print "OBTAINING '%s'" % (control_furl,)
            return self.tub.getReference(self.control_furl)
        d.addCallback(_record_control_furl)
        def _record_control(control_rref):
            self.control_rref = control_rref
            return control_rref.callRemote("wait_for_client_connections",
                                           self.numnodes+1)
        d.addCallback(_record_control)
        def _ready(res):
            #print "CLIENT READY"
            pass
        d.addCallback(_ready)
        return d

    def tearDown(self, passthrough):
        # the client node will shut down in a few seconds
        #os.remove(os.path.join(self.clientdir, "suicide_prevention_hotline"))
        log.msg("shutting down SystemTest services")
        d = defer.succeed(None)
        if self.proc:
            d.addCallback(lambda res: self.kill_client())
        d.addCallback(lambda res: self.sparent.stopService())
        d.addCallback(lambda res: eventual.flushEventualQueue())
        def _close_statsfile(res):
            self.statsfile.close()
        d.addCallback(_close_statsfile)
        d.addCallback(lambda res: passthrough)
        return d

    def add_service(self, s):
        s.setServiceParent(self.sparent)
        return s

    def make_introducer_and_vdrive(self):
        iv_basedir = os.path.join(self.basedir, "introducer_and_vdrive")
        os.mkdir(iv_basedir)
        iv = introducer_and_vdrive.IntroducerAndVdrive(basedir=iv_basedir)
        self.introducer_and_vdrive = self.add_service(iv)
        d = self.introducer_and_vdrive.when_tub_ready()
        return d

    def make_nodes(self):
        q = self.introducer_and_vdrive
        self.introducer_furl = q.urls["introducer"]
        self.vdrive_furl = q.urls["vdrive"]
        self.nodes = []
        for i in range(self.numnodes):
            nodedir = os.path.join(self.basedir, "node%d" % i)
            os.mkdir(nodedir)
            f = open(os.path.join(nodedir, "introducer.furl"), "w")
            f.write(self.introducer_furl)
            f.close()
            f = open(os.path.join(nodedir, "vdrive.furl"), "w")
            f.write(self.vdrive_furl)
            f.close()
            if self.discard_shares:
                # for this test, we tell the storage servers to throw out all
                # their stored data, since we're only testing upload and not
                # download.
                f = open(os.path.join(nodedir, "debug_no_storage"), "w")
                f.write("no_storage\n")
                f.close()
            c = self.add_service(client.Client(basedir=nodedir))
            self.nodes.append(c)
        # the peers will start running, eventually they will connect to each
        # other and the introducer_and_vdrive

    def touch_keepalive(self):
        f = open(self.keepalive_file, "w")
        f.write("""\
If the node notices this file at startup, it will poll every 5 seconds and
terminate if the file is more than 10 seconds old, or if it has been deleted.
If the test harness has an internal failure and neglects to kill off the node
itself, this helps to avoid leaving processes lying around. The contents of
this file are ignored.
        """)
        f.close()

    def start_client(self):
        # this returns a Deferred that fires with the client's control.furl
        log.msg("MAKING CLIENT")
        clientdir = self.clientdir = os.path.join(self.basedir, "client")
        quiet = StringIO()
        create_node.create_client(clientdir, {}, out=quiet)
        log.msg("DONE MAKING CLIENT")
        f = open(os.path.join(clientdir, "introducer.furl"), "w")
        f.write(self.introducer_furl + "\n")
        f.close()
        f = open(os.path.join(clientdir, "vdrive.furl"), "w")
        f.write(self.vdrive_furl + "\n")
        f.close()
        f = open(os.path.join(clientdir, "webport"), "w")
        # TODO: ideally we would set webport=0 and then ask the node what
        # port it picked. But at the moment it is not convenient to do this,
        # so we just pick a relatively unique one.
        webport = max(os.getpid(), 2000)
        f.write("tcp:%d:interface=127.0.0.1\n" % webport)
        f.close()
        self.webish_url = "http://localhost:%d" % webport
        if self.discard_shares:
            f = open(os.path.join(clientdir, "debug_no_storage"), "w")
            f.write("no_storage\n")
            f.close()
        if self.mode == "upload-self":
            f = open(os.path.join(clientdir, "push_to_ourselves"), "w")
            f.write("push_to_ourselves\n")
            f.close()
        self.keepalive_file = os.path.join(clientdir,
                                           "suicide_prevention_hotline")
        # now start updating the mtime.
        self.touch_keepalive()
        ts = internet.TimerService(4.0, self.touch_keepalive)
        ts.setServiceParent(self.sparent)

        pp = ClientWatcher()
        self.proc_done = pp.d = defer.Deferred()
        logfile = os.path.join(self.basedir, "client.log")
        cmd = ["twistd", "-y", "client.tac", "-l", logfile]
        env = os.environ.copy()
        self.proc = reactor.spawnProcess(pp, cmd[0], cmd, env, path=clientdir)
        log.msg("CLIENT STARTED")

        # now we wait for the client to get started. we're looking for the
        # control.furl file to appear.
        furl_file = os.path.join(clientdir, "control.furl")
        def _check():
            if pp.ended and pp.ended.value.status != 0:
                # the twistd process ends normally (with rc=0) if the child
                # is successfully launched. It ends abnormally (with rc!=0)
                # if the child cannot be launched.
                raise RuntimeError("process ended while waiting for startup")
            return os.path.exists(furl_file)
        d = self.poll(_check, 0.1)
        # once it exists, wait a moment before we read from it, just in case
        # it hasn't finished writing the whole thing. Ideally control.furl
        # would be created in some atomic fashion, or made non-readable until
        # it's ready, but I can't think of an easy way to do that, and I
        # think the chances that we'll observe a half-write are pretty low.
        def _stall(res):
            d2 = defer.Deferred()
            reactor.callLater(0.1, d2.callback, None)
            return d2
        d.addCallback(_stall)
        def _read(res):
            f = open(furl_file, "r")
            furl = f.read()
            return furl.strip()
        d.addCallback(_read)
        return d


    def kill_client(self):
        # returns a Deferred that fires when the process exits. This may only
        # be called once.
        try:
            self.proc.signalProcess("KILL")
        except error.ProcessExitedAlready:
            pass
        return self.proc_done


    def create_data(self, name, size):
        filename = os.path.join(self.basedir, name + ".data")
        f = open(filename, "wb")
        block = "a" * 8192
        while size > 0:
            l = min(size, 8192)
            f.write(block[:l])
            size -= l
        return filename

    def stash_stats(self, stats, name):
        self.statsfile.write("%s %s: %d\n" % (self.mode, name, stats['VmPeak']))
        self.stats[name] = stats['VmPeak']

    def POST(self, urlpath, **fields):
        url = self.webish_url + urlpath
        sepbase = "boogabooga"
        sep = "--" + sepbase
        form = []
        form.append(sep)
        form.append('Content-Disposition: form-data; name="_charset"')
        form.append('')
        form.append('UTF-8')
        form.append(sep)
        for name, value in fields.iteritems():
            if isinstance(value, tuple):
                filename, value = value
                form.append('Content-Disposition: form-data; name="%s"; '
                            'filename="%s"' % (name, filename))
            else:
                form.append('Content-Disposition: form-data; name="%s"' % name)
            form.append('')
            form.append(value)
            form.append(sep)
        form[-1] += "--"
        body = "\r\n".join(form) + "\r\n"
        headers = {"content-type": "multipart/form-data; boundary=%s" % sepbase,
                   }
        return getPage(url, method="POST", postdata=body,
                       headers=headers, followRedirect=False)

    def do_test(self):
        #print "CLIENT STARTED"
        #print "FURL", self.control_furl
        #print "RREF", self.control_rref
        #print
        kB = 1000; MB = 1000*1000
        files = {}
        uris = {}
        control = self.control_rref

        def _print_usage(res=None):
            d = control.callRemote("get_memory_usage")
            def _print(stats):
                print "VmSize: %9d  VmPeak: %9d" % (stats["VmSize"],
                                                    stats["VmPeak"])
                return stats
            d.addCallback(_print)
            return d

        def _do_upload(res, size):
            name = '%d' % size
            print
            print "uploading %s" % name
            if self.mode in ("upload", "upload-self"):
                files[name] = self.create_data(name, size)
                d = control.callRemote("upload_from_file_to_uri", files[name])
                def _done(uri):
                    os.remove(files[name])
                    del files[name]
                    return uri
                d.addCallback(_done)
            elif self.mode == "upload-POST":
                data = "a" * size
                url = "/vdrive/global"
                d = self.POST(url, t="upload", file=("%d.data" % size, data))
            else:
                raise RuntimeError("unknown mode=%s" % self.mode)
            def _complete(uri):
                uris[name] = uri
                print "uploaded %s" % name
            d.addCallback(_complete)
            return d

        d = _print_usage()
        d.addCallback(self.stash_stats, "0B")

        for i in range(10):
            d.addCallback(_do_upload, size=10*kB+i)
            d.addCallback(_print_usage)
        d.addCallback(self.stash_stats, "10kB")

        for i in range(3):
            d.addCallback(_do_upload, size=10*MB+i)
            d.addCallback(_print_usage)
        d.addCallback(self.stash_stats, "10MB")

        for i in range(3):
            d.addCallback(_do_upload, size=50*MB+i)
            d.addCallback(_print_usage)
        d.addCallback(self.stash_stats, "50MB")

        #for i in range(1):
        #    d.addCallback(_do_upload, size=100*MB+i)
        #    d.addCallback(_print_usage)
        #d.addCallback(self.stash_stats, "100MB")

        #d.addCallback(self.stall)
        def _done(res):
            print "FINISHING"
        d.addCallback(_done)
        return d

    def stall(self, res):
        d = defer.Deferred()
        reactor.callLater(5, d.callback, None)
        return d


class ClientWatcher(protocol.ProcessProtocol):
    ended = False
    def outReceived(self, data):
        print "OUT:", data
    def errReceived(self, data):
        print "ERR:", data
    def processEnded(self, reason):
        self.ended = reason
        self.d.callback(None)


if __name__ == '__main__':
    mode = "upload"
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    sf = SystemFramework("_test_memory", mode)
    sf.run()

