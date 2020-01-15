import os
from io import (
    BytesIO,
)
from os.path import dirname, join
from mock import patch, Mock
from six.moves import StringIO
from sys import getfilesystemencoding
from twisted.trial import unittest
from allmydata.scripts import runner
from allmydata.scripts.run_common import (
    identify_node_type,
    DaemonizeTahoeNodePlugin,
    MyTwistdConfig,
)
from allmydata.scripts.tahoe_daemonize import (
    DaemonizeOptions,
)


class Util(unittest.TestCase):
    def setUp(self):
        self.twistd_options = MyTwistdConfig()
        self.twistd_options.parseOptions(["DaemonizeTahoeNode"])
        self.options = self.twistd_options.subOptions

    def test_node_type_nothing(self):
        tmpdir = self.mktemp()
        base = dirname(tmpdir).decode(getfilesystemencoding())

        t = identify_node_type(base)

        self.assertIs(None, t)

    def test_node_type_introducer(self):
        tmpdir = self.mktemp()
        base = dirname(tmpdir).decode(getfilesystemencoding())
        with open(join(dirname(tmpdir), 'introducer.tac'), 'w') as f:
            f.write("test placeholder")

        t = identify_node_type(base)

        self.assertEqual(u"introducer", t)

    def test_daemonize(self):
        tmpdir = self.mktemp()
        plug = DaemonizeTahoeNodePlugin('client', tmpdir)

        with patch('twisted.internet.reactor') as r:
            def call(fn, *args, **kw):
                fn()
            r.stop = lambda: None
            r.callWhenRunning = call
            service = plug.makeService(self.options)
            service.parent = Mock()
            service.startService()

        self.assertTrue(service is not None)

    def test_daemonize_no_keygen(self):
        tmpdir = self.mktemp()
        stderr = BytesIO()
        plug = DaemonizeTahoeNodePlugin('key-generator', tmpdir)

        with patch('twisted.internet.reactor') as r:
            def call(fn, *args, **kw):
                d = fn()
                d.addErrback(lambda _: None)  # ignore the error we'll trigger
            r.callWhenRunning = call
            service = plug.makeService(self.options)
            service.stderr = stderr
            service.parent = Mock()
            # we'll raise ValueError because there's no key-generator
            # .. BUT we do this in an async function called via
            # "callWhenRunning" .. hence using a hook
            d = service.set_hook('running')
            service.startService()
            def done(f):
                self.assertIn(
                    "key-generator support removed",
                    stderr.getvalue(),
                )
                return None
            d.addBoth(done)
            return d

    def test_daemonize_unknown_nodetype(self):
        tmpdir = self.mktemp()
        plug = DaemonizeTahoeNodePlugin('an-unknown-service', tmpdir)

        with patch('twisted.internet.reactor') as r:
            def call(fn, *args, **kw):
                fn()
            r.stop = lambda: None
            r.callWhenRunning = call
            service = plug.makeService(self.options)
            service.parent = Mock()
            with self.assertRaises(ValueError) as ctx:
                service.startService()
            self.assertIn(
                "unknown nodetype",
                str(ctx.exception)
            )

    def test_daemonize_options(self):
        parent = runner.Options()
        opts = DaemonizeOptions()
        opts.parent = parent
        opts.parseArgs()

        # just gratuitous coverage, ensureing we don't blow up on
        # these methods.
        opts.getSynopsis()
        opts.getUsage()


class RunDaemonizeTests(unittest.TestCase):

    def setUp(self):
        # no test should change our working directory
        self._working = os.path.abspath('.')
        d = super(RunDaemonizeTests, self).setUp()
        self._reactor = patch('twisted.internet.reactor')
        self._reactor.stop = lambda: None
        self._twistd = patch('allmydata.scripts.run_common.twistd')
        self.node_dir = self.mktemp()
        os.mkdir(self.node_dir)
        for cm in [self._reactor, self._twistd]:
            cm.__enter__()
        return d

    def tearDown(self):
        d = super(RunDaemonizeTests, self).tearDown()
        for cm in [self._reactor, self._twistd]:
            cm.__exit__(None, None, None)
        # Note: if you raise an exception (e.g. via self.assertEqual
        # or raise RuntimeError) it is apparently just ignored and the
        # test passes anyway...
        if self._working != os.path.abspath('.'):
            print("WARNING: a test just changed the working dir; putting it back")
            os.chdir(self._working)
        return d

    def _placeholder_nodetype(self, nodetype):
        fname = join(self.node_dir, '{}.tac'.format(nodetype))
        with open(fname, 'w') as f:
            f.write("test placeholder")

    def test_daemonize_defaults(self):
        self._placeholder_nodetype('introducer')

        config = runner.parse_or_exit_with_explanation([
            # have to do this so the tests don't much around in
            # ~/.tahoe (the default)
            '--node-directory', self.node_dir,
            'daemonize',
        ])
        i, o, e = StringIO(), StringIO(), StringIO()
        with patch('allmydata.scripts.runner.sys') as s:
            exit_code = [None]
            def _exit(code):
                exit_code[0] = code
            s.exit = _exit
            runner.dispatch(config, i, o, e)

            self.assertEqual(0, exit_code[0])

    def test_daemonize_wrong_nodetype(self):
        self._placeholder_nodetype('invalid')

        config = runner.parse_or_exit_with_explanation([
            # have to do this so the tests don't much around in
            # ~/.tahoe (the default)
            '--node-directory', self.node_dir,
            'daemonize',
        ])
        i, o, e = StringIO(), StringIO(), StringIO()
        with patch('allmydata.scripts.runner.sys') as s:
            exit_code = [None]
            def _exit(code):
                exit_code[0] = code
            s.exit = _exit
            runner.dispatch(config, i, o, e)

            self.assertEqual(0, exit_code[0])

    def test_daemonize_run(self):
        self._placeholder_nodetype('client')

        config = runner.parse_or_exit_with_explanation([
            # have to do this so the tests don't much around in
            # ~/.tahoe (the default)
            '--node-directory', self.node_dir,
            'daemonize',
        ])
        with patch('allmydata.scripts.runner.sys') as s:
            exit_code = [None]
            def _exit(code):
                exit_code[0] = code
            s.exit = _exit
            from allmydata.scripts.tahoe_daemonize import daemonize
            daemonize(config)
