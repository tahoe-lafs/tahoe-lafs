from ...util.encodingutil import unicode_to_argv
from ...scripts import runner
from ..common_util import ReallyEqualMixin, run_cli, run_cli_unicode

def parse_options(basedir, command, args):
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
        client_dir = unicode_to_argv(self.get_clientdir(i=client_num))
        nodeargs = [ b"--node-directory", client_dir ]
        return run_cli(verb, *args, nodeargs=nodeargs, **kwargs)
