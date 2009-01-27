
from twisted.trial import unittest

from cStringIO import StringIO
from twisted.python import runtime
from twisted.internet import utils
import os.path, re
from allmydata.scripts import runner
from allmydata.util import fileutil, pollmixin

from allmydata.test import common_util
import allmydata

bintahoe = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(allmydata.__file__))), 'bin', 'tahoe')

class TheRightCode(unittest.TestCase, common_util.SignalMixin):
    def test_path(self):
        if not os.path.exists(bintahoe):
            raise unittest.SkipTest("The bin/tahoe script isn't to be found in the expected location, and I don't want to test a 'tahoe' executable that I find somewhere else, in case it isn't the right executable for this version of tahoe.")
        d = utils.getProcessOutputAndValue(bintahoe, args=["--version-and-path"], env=os.environ)
        def _cb(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 0)

            # Fail unless the allmydata-tahoe package is *this* version *and* was loaded from *this* source directory.
            required_ver_and_path = "allmydata-tahoe: %s (%s)" % (allmydata.__version__, os.path.dirname(os.path.dirname(allmydata.__file__)))
            self.failUnless(out.startswith(required_ver_and_path), (out, err, rc_or_sig))
        d.addCallback(_cb)
        return d

class CreateNode(unittest.TestCase, common_util.SignalMixin):
    def workdir(self, name):
        basedir = os.path.join("test_runner", "CreateNode", name)
        fileutil.make_dirs(basedir)
        return basedir

    def test_client(self):
        if not os.path.exists(bintahoe):
            raise unittest.SkipTest("The bin/tahoe script isn't to be found in the expected location, and I don't want to test a 'tahoe' executable that I find somewhere else, in case it isn't the right executable for this version of tahoe.")
        basedir = self.workdir("test_client")
        c1 = os.path.join(basedir, "c1")
        d = utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "create-client", "--basedir", c1], env=os.environ)
        def _cb(res):
            out, err, rc_or_sig = res
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.
            self.failUnlessEqual(out, "")
            self.failUnlessEqual(rc_or_sig, 0)
            self.failUnless(os.path.exists(c1))
            self.failUnless(os.path.exists(os.path.join(c1, "tahoe-client.tac")))
        d.addCallback(_cb)

        def _then_try_again(unused=None):
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "create-client", "--basedir", c1], env=os.environ)
        d.addCallback(_then_try_again)

        def _cb2(res):
            out, err, rc_or_sig = res
            # creating the client a second time should throw an exception
            self.failIfEqual(rc_or_sig, 0, str((out, err, rc_or_sig)))
            self.failUnlessEqual(out, "")
            self.failUnless("is not empty." in err)

            # Fail if there is a line that doesn't end with a PUNCTUATION MARK.
            self.failIf(re.search("[^\.!?]\n", err), err)
        d.addCallback(_cb2)

        c2 = os.path.join(basedir, "c2")
        def _then_try_new_dir(unused=None):
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "create-client", c2], env=os.environ)
        d.addCallback(_then_try_new_dir)

        def _cb3(res):
            out, err, rc_or_sig = res
            self.failUnless(os.path.exists(c2))
            self.failUnless(os.path.exists(os.path.join(c2, "tahoe-client.tac")))
        d.addCallback(_cb3)

        def _then_try_badarg(unused=None):
            return utils.getProcessOutputAndValue(bintahoe, args=["create-client", "basedir", "extraarg"], env=os.environ)
        d.addCallback(_then_try_badarg)

        def _cb4(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 1)
            self.failUnless(out.startswith("Usage"), out)
        d.addCallback(_cb4)
        return d

    def test_introducer(self):
        if not os.path.exists(bintahoe):
            raise unittest.SkipTest("The bin/tahoe script isn't to be found in the expected location, and I don't want to test a 'tahoe' executable that I find somewhere else, in case it isn't the right executable for this version of tahoe.")
        basedir = self.workdir("test_introducer")
        c1 = os.path.join(basedir, "c1")
        d = utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "create-introducer", "--basedir", c1], env=os.environ)
        def _cb(res):
            out, err, rc_or_sig = res
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.
            self.failUnlessEqual(out, "")
            self.failUnlessEqual(rc_or_sig, 0)
            self.failUnless(os.path.exists(c1))
            self.failUnless(os.path.exists(os.path.join(c1,
                                                        "tahoe-introducer.tac")))
        d.addCallback(_cb)

        def _then_try_again(unused=None):
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "create-introducer", "--basedir", c1], env=os.environ)
        d.addCallback(_then_try_again)

        def _cb2(res):
            out, err, rc_or_sig = res
            # creating the introducer a second time should throw an exception
            self.failIfEqual(rc_or_sig, 0)
            self.failUnlessEqual(out, "")
            self.failUnless("is not empty" in err)

            # Fail if there is a line that doesn't end with a PUNCTUATION MARK.
            self.failIf(re.search("[^\.!?]\n", err), err)
        d.addCallback(_cb2)

        c2 = os.path.join(basedir, "c2")
        def _then_try_new_dir(unused=None):
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "create-introducer", c2], env=os.environ)
        d.addCallback(_then_try_new_dir)

        def _cb3(res):
            out, err, rc_or_sig = res
            self.failUnless(os.path.exists(c2))
            self.failUnless(os.path.exists(os.path.join(c2,
                                                        "tahoe-introducer.tac")))
        d.addCallback(_cb3)

        def _then_try_badarg(unused=None):
            return utils.getProcessOutputAndValue(bintahoe, args=["create-introducer", "basedir", "extraarg"], env=os.environ)
        d.addCallback(_then_try_badarg)

        def _cb4(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 1)
            self.failUnless(out.startswith("Usage"), out)
        d.addCallback(_cb4)

        def _then_try_badarg_again(unused=None):
            return utils.getProcessOutputAndValue(bintahoe, args=["create-introducer"], env=os.environ)
        d.addCallback(_then_try_badarg_again)

        def _cb5(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 1)
            self.failUnless(out.startswith("Usage"), out)
        d.addCallback(_cb5)
        return d

class RunNode(unittest.TestCase, pollmixin.PollMixin, common_util.SignalMixin):
    def workdir(self, name):
        basedir = os.path.join("test_runner", "RunNode", name)
        fileutil.make_dirs(basedir)
        return basedir

    def test_introducer(self):
        if not os.path.exists(bintahoe):
            raise unittest.SkipTest("The bin/tahoe script isn't to be found in the expected location, and I don't want to test a 'tahoe' executable that I find somewhere else, in case it isn't the right executable for this version of tahoe.")
        if runtime.platformType == "win32":
            # twistd on windows doesn't daemonize. cygwin works normally.
            raise unittest.SkipTest("twistd does not fork under windows")
        basedir = self.workdir("test_introducer")
        c1 = os.path.join(basedir, "c1")
        HOTLINE_FILE = os.path.join(c1, "suicide_prevention_hotline")
        TWISTD_PID_FILE = os.path.join(c1, "twistd.pid")
        INTRODUCER_FURL_FILE = os.path.join(c1, "introducer.furl")

        d = utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "create-introducer", "--basedir", c1], env=os.environ)
        def _cb(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 0)
            # by writing this file, we get ten seconds before the node will
            # exit. This insures that even if the test fails (and the 'stop'
            # command doesn't work), the client should still terminate.
            open(HOTLINE_FILE, "w").write("")
            # now it's safe to start the node
        d.addCallback(_cb)

        def _then_start_the_node(res):
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "start", c1], env=os.environ)
        d.addCallback(_then_start_the_node)

        def _cb2(res):
            out, err, rc_or_sig = res

            open(HOTLINE_FILE, "w").write("")
            errstr = "rc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.

            # the parent (twistd) has exited. However, twistd writes the pid
            # from the child, not the parent, so we can't expect twistd.pid
            # to exist quite yet.

            # the node is running, but it might not have made it past the
            # first reactor turn yet, and if we kill it too early, it won't
            # remove the twistd.pid file. So wait until it does something
            # that we know it won't do until after the first turn.
        d.addCallback(_cb2)

        def _node_has_started():
            return os.path.exists(INTRODUCER_FURL_FILE)
        d.addCallback(lambda res: self.poll(_node_has_started))

        def _started(res):
            open(HOTLINE_FILE, "w").write("")
            self.failUnless(os.path.exists(TWISTD_PID_FILE))
            # rm this so we can detect when the second incarnation is ready
            os.unlink(INTRODUCER_FURL_FILE)
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "restart", c1], env=os.environ)
        d.addCallback(_started)

        def _then(res):
            out, err, rc_or_sig = res
            open(HOTLINE_FILE, "w").write("")
            errstr = "rc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.
        d.addCallback(_then)

        # again, the second incarnation of the node might not be ready yet,
        # so poll until it is
        d.addCallback(lambda res: self.poll(_node_has_started))

        # now we can kill it. TODO: On a slow machine, the node might kill
        # itself before we get a chance too, especially if spawning the
        # 'tahoe stop' command takes a while.
        def _stop(res):
            open(HOTLINE_FILE, "w").write("")
            self.failUnless(os.path.exists(TWISTD_PID_FILE))

            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "stop", c1], env=os.environ)
        d.addCallback(_stop)

        def _after_stopping(res):
            out, err, rc_or_sig = res
            open(HOTLINE_FILE, "w").write("")
            # the parent has exited by now
            errstr = "rc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.
            # the parent was supposed to poll and wait until it sees
            # twistd.pid go away before it exits, so twistd.pid should be
            # gone by now.
            self.failIf(os.path.exists(TWISTD_PID_FILE))
        d.addCallback(_after_stopping)

        def _remove_hotline(res):
            os.unlink(HOTLINE_FILE)
            return res
        d.addBoth(_remove_hotline)
        return d

    def test_client_no_noise(self):
        if not os.path.exists(bintahoe):
            raise unittest.SkipTest("The bin/tahoe script isn't to be found in the expected location, and I don't want to test a 'tahoe' executable that I find somewhere else, in case it isn't the right executable for this version of tahoe.")
        basedir = self.workdir("test_client_no_noise")
        c1 = os.path.join(basedir, "c1")
        HOTLINE_FILE = os.path.join(c1, "suicide_prevention_hotline")
        TWISTD_PID_FILE = os.path.join(c1, "twistd.pid")
        PORTNUMFILE = os.path.join(c1, "client.port")

        d = utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "create-client", "--basedir", c1, "--webport", "0"], env=os.environ)
        def _cb(res):
            out, err, rc_or_sig = res
            errstr = "cc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            assert rc_or_sig == 0, errstr
            self.failUnlessEqual(rc_or_sig, 0)
            # By writing this file, we get forty seconds before the client will exit. This insures
            # that even if the 'stop' command doesn't work (and the test fails), the client should
            # still terminate.
            open(HOTLINE_FILE, "w").write("")
            open(os.path.join(c1, "introducer.furl"), "w").write("pb://xrndsskn2zuuian5ltnxrte7lnuqdrkz@127.0.0.1:55617/introducer\n")
            # now it's safe to start the node
        d.addCallback(_cb)

        def _start(res):
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "start", c1], env=os.environ)
        d.addCallback(_start)

        def _cb2(res):
            out, err, rc_or_sig = res
            errstr = "cc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            open(HOTLINE_FILE, "w").write("")
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr) # If you emit noise, you fail this test.
            self.failUnlessEqual(err, "", errstr)

            # the parent (twistd) has exited. However, twistd writes the pid
            # from the child, not the parent, so we can't expect twistd.pid
            # to exist quite yet.

            # the node is running, but it might not have made it past the
            # first reactor turn yet, and if we kill it too early, it won't
            # remove the twistd.pid file. So wait until it does something
            # that we know it won't do until after the first turn.
        d.addCallback(_cb2)

        def _node_has_started():
            return os.path.exists(PORTNUMFILE)
        d.addCallback(lambda res: self.poll(_node_has_started))

        # now we can kill it. TODO: On a slow machine, the node might kill
        # itself before we get a chance too, especially if spawning the
        # 'tahoe stop' command takes a while.
        def _stop(res):
            self.failUnless(os.path.exists(TWISTD_PID_FILE), (TWISTD_PID_FILE, os.listdir(os.path.dirname(TWISTD_PID_FILE))))
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "stop", c1], env=os.environ)
        d.addCallback(_stop)
        return d
    test_client_no_noise.todo = "We submitted a patch to Nevow to silence this warning: http://divmod.org/trac/ticket/2830"

    def test_client(self):
        if not os.path.exists(bintahoe):
            raise unittest.SkipTest("The bin/tahoe script isn't to be found in the expected location, and I don't want to test a 'tahoe' executable that I find somewhere else, in case it isn't the right executable for this version of tahoe.")
        if runtime.platformType == "win32":
            # twistd on windows doesn't daemonize. cygwin works normally.
            raise unittest.SkipTest("twistd does not fork under windows")
        basedir = self.workdir("test_client")
        c1 = os.path.join(basedir, "c1")
        HOTLINE_FILE = os.path.join(c1, "suicide_prevention_hotline")
        TWISTD_PID_FILE = os.path.join(c1, "twistd.pid")
        PORTNUMFILE = os.path.join(c1, "client.port")

        d = utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "create-client", "--basedir", c1, "--webport", "0"], env=os.environ)
        def _cb(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 0)
            # By writing this file, we get forty seconds before the client will exit. This insures
            # that even if the 'stop' command doesn't work (and the test fails), the client should
            # still terminate.
            open(HOTLINE_FILE, "w").write("")
            open(os.path.join(c1, "introducer.furl"), "w").write("pb://xrndsskn2zuuian5ltnxrte7lnuqdrkz@127.0.0.1:55617/introducer\n")
            # now it's safe to start the node
        d.addCallback(_cb)

        def _start(res):
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "start", c1], env=os.environ)
        d.addCallback(_start)

        def _cb2(res):
            out, err, rc_or_sig = res
            open(HOTLINE_FILE, "w").write("")
            errstr = "cc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.

            # the parent (twistd) has exited. However, twistd writes the pid
            # from the child, not the parent, so we can't expect twistd.pid
            # to exist quite yet.

            # the node is running, but it might not have made it past the
            # first reactor turn yet, and if we kill it too early, it won't
            # remove the twistd.pid file. So wait until it does something
            # that we know it won't do until after the first turn.
        d.addCallback(_cb2)

        def _node_has_started():
            return os.path.exists(PORTNUMFILE)
        d.addCallback(lambda res: self.poll(_node_has_started))

        def _started(res):
            open(HOTLINE_FILE, "w").write("")
            self.failUnless(os.path.exists(TWISTD_PID_FILE))
            # rm this so we can detect when the second incarnation is ready
            os.unlink(PORTNUMFILE)

            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "restart", c1], env=os.environ)
        d.addCallback(_started)

        def _cb3(res):
            out, err, rc_or_sig = res

            open(HOTLINE_FILE, "w").write("")
            errstr = "rc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.
        d.addCallback(_cb3)

        # again, the second incarnation of the node might not be ready yet,
        # so poll until it is
        d.addCallback(lambda res: self.poll(_node_has_started))

        # now we can kill it. TODO: On a slow machine, the node might kill
        # itself before we get a chance too, especially if spawning the
        # 'tahoe stop' command takes a while.
        def _stop(res):
            open(HOTLINE_FILE, "w").write("")
            self.failUnless(os.path.exists(TWISTD_PID_FILE), (TWISTD_PID_FILE, os.listdir(os.path.dirname(TWISTD_PID_FILE))))
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "stop", c1], env=os.environ)
        d.addCallback(_stop)

        def _cb4(res):
            out, err, rc_or_sig = res

            open(HOTLINE_FILE, "w").write("")
            # the parent has exited by now
            errstr = "rc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.
            # the parent was supposed to poll and wait until it sees
            # twistd.pid go away before it exits, so twistd.pid should be
            # gone by now.
            self.failIf(os.path.exists(TWISTD_PID_FILE))
        d.addCallback(_cb4)
        def _remove_hotline(res):
            os.unlink(HOTLINE_FILE)
            return res
        d.addBoth(_remove_hotline)
        return d

    def test_baddir(self):
        if not os.path.exists(bintahoe):
            raise unittest.SkipTest("The bin/tahoe script isn't to be found in the expected location, and I don't want to test a 'tahoe' executable that I find somewhere else, in case it isn't the right executable for this version of tahoe.")
        basedir = self.workdir("test_baddir")
        fileutil.make_dirs(basedir)

        d = utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "start", "--basedir", basedir], env=os.environ)
        def _cb(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 1)
            self.failUnless("does not look like a node directory" in err)
        d.addCallback(_cb)

        def _then_stop_it(res):
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "stop", "--basedir", basedir], env=os.environ)
        d.addCallback(_then_stop_it)

        def _cb2(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 2)
            self.failUnless("does not look like a running node directory" in err)
        d.addCallback(_cb2)

        def _then_start_in_bogus_basedir(res):
            not_a_dir = os.path.join(basedir, "bogus")
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "start", "--basedir", not_a_dir], env=os.environ)
        d.addCallback(_then_start_in_bogus_basedir)

        def _cb3(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 1)
            self.failUnless("does not look like a directory at all" in err, err)
        d.addCallback(_cb3)
        return d

    def test_keygen(self):
        if not os.path.exists(bintahoe):
            raise unittest.SkipTest("The bin/tahoe script isn't to be found in the expected location, and I don't want to test a 'tahoe' executable that I find somewhere else, in case it isn't the right executable for this version of tahoe.")
        if runtime.platformType == "win32":
            # twistd on windows doesn't daemonize. cygwin works normally.
            raise unittest.SkipTest("twistd does not fork under windows")
        basedir = self.workdir("test_keygen")
        c1 = os.path.join(basedir, "c1")
        TWISTD_PID_FILE = os.path.join(c1, "twistd.pid")
        KEYGEN_FURL_FILE = os.path.join(c1, "key_generator.furl")

        d = utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "create-key-generator", "--basedir", c1], env=os.environ)
        def _cb(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 0)
        d.addCallback(_cb)

        def _start(res):
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "start", c1], env=os.environ)
        d.addCallback(_start)

        def _cb2(res):
            out, err, rc_or_sig = res
            errstr = "rc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.

            # the parent (twistd) has exited. However, twistd writes the pid
            # from the child, not the parent, so we can't expect twistd.pid
            # to exist quite yet.

            # the node is running, but it might not have made it past the
            # first reactor turn yet, and if we kill it too early, it won't
            # remove the twistd.pid file. So wait until it does something
            # that we know it won't do until after the first turn.
        d.addCallback(_cb2)

        def _node_has_started():
            return os.path.exists(KEYGEN_FURL_FILE)
        d.addCallback(lambda res: self.poll(_node_has_started))

        def _started(res):
            self.failUnless(os.path.exists(TWISTD_PID_FILE))
            # rm this so we can detect when the second incarnation is ready
            os.unlink(KEYGEN_FURL_FILE)
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "restart", c1], env=os.environ)
        d.addCallback(_started)

        def _cb3(res):
            out, err, rc_or_sig = res
            errstr = "rc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.
        d.addCallback(_cb3)

        # again, the second incarnation of the node might not be ready yet,
        # so poll until it is
        d.addCallback(lambda res: self.poll(_node_has_started))

        # now we can kill it. TODO: On a slow machine, the node might kill
        # itself before we get a chance too, especially if spawning the
        # 'tahoe stop' command takes a while.
        def _stop(res):
            self.failUnless(os.path.exists(TWISTD_PID_FILE))
            return utils.getProcessOutputAndValue(bintahoe, args=["--quiet", "stop", c1], env=os.environ)
        d.addCallback(_stop)

        def _cb4(res):
            out, err, rc_or_sig = res
            # the parent has exited by now
            errstr = "rc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.
            # the parent was supposed to poll and wait until it sees
            # twistd.pid go away before it exits, so twistd.pid should be
            # gone by now.
            self.failIf(os.path.exists(TWISTD_PID_FILE))
        d.addCallback(_cb4)
        return d
