"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from six import ensure_str, ensure_text

from ...scripts import runner
from ..common_util import ReallyEqualMixin, run_cli, run_cli_unicode

def parse_options(basedir, command, args):
    args = [ensure_text(s) for s in args]
    o = runner.Options()
    o.parseOptions(["--node-directory", basedir, command] + args)
    while hasattr(o, "subOptions"):
        o = o.subOptions
    return o

class CLITestMixin(ReallyEqualMixin):
    """
    A mixin for use with ``GridTestMixin`` to execute CLI commands against
    nodes created by methods of that mixin.
    """
    def do_cli_unicode(self, verb, argv, client_num=0, **kwargs):
        """
        Run a Tahoe-LAFS CLI command.

        :param verb: See ``run_cli_unicode``.

        :param argv: See ``run_cli_unicode``.

        :param int client_num: The number of the ``GridTestMixin``-created
            node against which to execute the command.

        :param kwargs: Additional keyword arguments to pass to
            ``run_cli_unicode``.
        """
        # client_num is used to execute client CLI commands on a specific
        # client.
        client_dir = self.get_clientdir(i=client_num)
        nodeargs = [ u"--node-directory", client_dir ]
        return run_cli_unicode(verb, argv, nodeargs=nodeargs, **kwargs)


    def do_cli(self, verb, *args, **kwargs):
        """
        Like ``do_cli_unicode`` but work with ``bytes`` everywhere instead of
        ``unicode``.

        Where possible, prefer ``do_cli_unicode``.
        """
        # client_num is used to execute client CLI commands on a specific
        # client.
        client_num = kwargs.pop("client_num", 0)
        # If we were really going to launch a child process then
        # `unicode_to_argv` would be the right thing to do here.  However,
        # we're just going to call some Python functions directly and those
        # Python functions want native strings.  So ignore the requirements
        # for passing arguments to another process and make sure this argument
        # is a native string.
        verb = ensure_str(verb)
        args = [ensure_str(arg) for arg in args]
        client_dir = ensure_str(self.get_clientdir(i=client_num))
        nodeargs = [ "--node-directory", client_dir ]
        return run_cli(verb, *args, nodeargs=nodeargs, **kwargs)
