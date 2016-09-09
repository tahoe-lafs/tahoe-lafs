from cStringIO import StringIO
from twisted.internet import threads # CLI tests use deferToThread
from ...util.assertutil import precondition
from ...util.encodingutil import unicode_to_argv
from ...scripts import runner
from ..common_util import ReallyEqualMixin

def parse_options(basedir, command, args):
    o = runner.Options()
    o.parseOptions(["--node-directory", basedir, command] + args)
    while hasattr(o, "subOptions"):
        o = o.subOptions
    return o

class CLITestMixin(ReallyEqualMixin):
    def do_cli(self, verb, *args, **kwargs):
        # client_num is used to execute client CLI commands on a specific
        # client.
        client_num = kwargs.get("client_num", 0)
        client_dir = unicode_to_argv(self.get_clientdir(i=client_num))
        nodeargs = [ "--node-directory", client_dir ]
        return run_cli(verb, nodeargs=nodeargs, *args, **kwargs)

def run_cli(verb, *args, **kwargs):
    precondition(not [True for arg in args if not isinstance(arg, str)],
                 "arguments to do_cli must be strs -- convert using unicode_to_argv", args=args)
    nodeargs = kwargs.get("nodeargs", [])
    argv = nodeargs + [verb] + list(args)
    stdin = kwargs.get("stdin", "")
    stdout, stderr = StringIO(), StringIO()
    d = threads.deferToThread(runner.runner, argv, run_by_human=False,
                              stdin=StringIO(stdin),
                              stdout=stdout, stderr=stderr)
    def _done(rc):
        return rc, stdout.getvalue(), stderr.getvalue()
    d.addCallback(_done)
    return d
