from __future__ import print_function
import os
import shutil
import subprocess
from os.path import join
from mock import patch
from StringIO import StringIO

from twisted.trial import unittest
from allmydata.scripts import runner


#@patch('twisted.internet.reactor')
@patch('allmydata.scripts.tahoe_start.subprocess')
class RunStartTests(unittest.TestCase):

    def setUp(self):
        d = super(RunStartTests, self).setUp()
        self.node_dir = self.mktemp()
        os.mkdir(self.node_dir)
        return d

    def _placeholder_nodetype(self, nodetype):
        fname = join(self.node_dir, '{}.tac'.format(nodetype))
        with open(fname, 'w') as f:
            f.write("test placeholder")

    def _pid_file(self, pid):
        fname = join(self.node_dir, 'twistd.pid')
        with open(fname, 'w') as f:
            f.write(u"{}\n".format(pid))

    def _logs(self, logs):
        os.mkdir(join(self.node_dir, 'logs'))
        fname = join(self.node_dir, 'logs', 'twistd.log')
        with open(fname, 'w') as f:
            f.write(logs)

    def test_start_defaults(self, _subprocess):
        self._placeholder_nodetype('client')
        self._pid_file(1234)
        self._logs('one log\ntwo log\nred log\nblue log\n')

        config = runner.parse_or_exit_with_explanation([
            # have to do this so the tests don't muck around in
            # ~/.tahoe (the default)
            '--node-directory', self.node_dir,
            'start',
        ])
        i, o, e = StringIO(), StringIO(), StringIO()
        try:
            with patch('allmydata.scripts.tahoe_start.os'):
                with patch('allmydata.scripts.runner.sys') as s:
                    exit_code = [None]
                    def _exit(code):
                        exit_code[0] = code
                    s.exit = _exit

                    def launch(*args, **kw):
                        with open(join(self.node_dir, 'logs', 'twistd.log'), 'a') as f:
                            f.write('client running\n')  # "the magic"
                    _subprocess.check_call = launch
                    runner.dispatch(config, i, o, e)
        except Exception:
            pass

        self.assertEqual([0], exit_code)
        self.assertTrue('Node has started' in o.getvalue())

    def test_start_fails(self, _subprocess):
        self._placeholder_nodetype('client')
        self._logs('existing log line\n')

        config = runner.parse_or_exit_with_explanation([
            # have to do this so the tests don't muck around in
            # ~/.tahoe (the default)
            '--node-directory', self.node_dir,
            'start',
        ])

        i, o, e = StringIO(), StringIO(), StringIO()
        with patch('allmydata.scripts.tahoe_start.time') as t:
            with patch('allmydata.scripts.runner.sys') as s:
                exit_code = [None]
                def _exit(code):
                    exit_code[0] = code
                s.exit = _exit

                thetime = [0]
                def _time():
                    thetime[0] += 0.1
                    return thetime[0]
                t.time = _time

                def launch(*args, **kw):
                    with open(join(self.node_dir, 'logs', 'twistd.log'), 'a') as f:
                        f.write('a new log line\n')
                _subprocess.check_call = launch

                runner.dispatch(config, i, o, e)

        # should print out the collected logs and an error-code
        self.assertTrue("a new log line" in o.getvalue())
        self.assertEqual([1], exit_code)

    def test_start_subprocess_fails(self, _subprocess):
        self._placeholder_nodetype('client')
        self._logs('existing log line\n')

        config = runner.parse_or_exit_with_explanation([
            # have to do this so the tests don't muck around in
            # ~/.tahoe (the default)
            '--node-directory', self.node_dir,
            'start',
        ])

        i, o, e = StringIO(), StringIO(), StringIO()
        with patch('allmydata.scripts.tahoe_start.time'):
            with patch('allmydata.scripts.runner.sys') as s:
                # undo patch for the exception-class
                _subprocess.CalledProcessError = subprocess.CalledProcessError
                exit_code = [None]
                def _exit(code):
                    exit_code[0] = code
                s.exit = _exit

                def launch(*args, **kw):
                    raise subprocess.CalledProcessError(42, "tahoe")
                _subprocess.check_call = launch

                runner.dispatch(config, i, o, e)

        # should get our "odd" error-code
        self.assertEqual([42], exit_code)

    def test_start_help(self, _subprocess):
        self._placeholder_nodetype('client')

        std = StringIO()
        with patch('sys.stdout') as stdo:
            stdo.write = std.write
            try:
                runner.parse_or_exit_with_explanation([
                    # have to do this so the tests don't muck around in
                    # ~/.tahoe (the default)
                    '--node-directory', self.node_dir,
                    'start',
                    '--help',
                ], stdout=std)
                self.fail("Should get exit")
            except SystemExit as e:
                print(e)

        self.assertIn(
            "Usage:",
            std.getvalue()
        )

    def test_start_unknown_node_type(self, _subprocess):
        self._placeholder_nodetype('bogus')

        config = runner.parse_or_exit_with_explanation([
            # have to do this so the tests don't muck around in
            # ~/.tahoe (the default)
            '--node-directory', self.node_dir,
            'start',
        ])

        i, o, e = StringIO(), StringIO(), StringIO()
        with patch('allmydata.scripts.runner.sys') as s:
            exit_code = [None]
            def _exit(code):
                exit_code[0] = code
            s.exit = _exit

            runner.dispatch(config, i, o, e)

        # should print out the collected logs and an error-code
        self.assertIn(
            "is not a recognizable node directory",
            e.getvalue()
        )
        self.assertEqual([1], exit_code)

    def test_start_nodedir_not_dir(self, _subprocess):
        shutil.rmtree(self.node_dir)
        assert not os.path.isdir(self.node_dir)

        config = runner.parse_or_exit_with_explanation([
            # have to do this so the tests don't muck around in
            # ~/.tahoe (the default)
            '--node-directory', self.node_dir,
            'start',
        ])

        i, o, e = StringIO(), StringIO(), StringIO()
        with patch('allmydata.scripts.runner.sys') as s:
            exit_code = [None]
            def _exit(code):
                exit_code[0] = code
            s.exit = _exit

            runner.dispatch(config, i, o, e)

        # should print out the collected logs and an error-code
        self.assertIn(
            "does not look like a directory at all",
            e.getvalue()
        )
        self.assertEqual([1], exit_code)
