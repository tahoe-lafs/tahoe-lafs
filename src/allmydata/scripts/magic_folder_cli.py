
import os, json
from cStringIO import StringIO
from twisted.python import usage, failure

from allmydata.scripts.common import BaseOptions
from .common import BaseOptions, BasedirOptions, get_aliases
from .cli import MakeDirectoryOptions, ListOptions, LnOptions
import tahoe_ls, tahoe_mv

class CreateOptions(BasedirOptions):
    nickname = None
    localdir = None
    synopsis = "MAGIC_ALIAS: [NICKNAME LOCALDIR]"
    def parseArgs(self, alias, nickname=None, localdir=None):
        BasedirOptions.parseArgs(self)
        self.alias = alias
        self.nickname = nickname
        self.localdir = localdir
        if self.nickname and not self.localdir:
            raise usage.UsageError("must provide both")
        node_url_file = os.path.join(self['node-directory'], "node.url")
        self['node-url'] = open(node_url_file, "r").read().strip()

def create(options):
    from allmydata.scripts import tahoe_add_alias
    rc = tahoe_add_alias.create_alias(options)
    return rc

class InviteOptions(BasedirOptions):
    nickname = None
    synopsis = "MAGIC_ALIAS: NICKNAME"
    stdin = StringIO("")
    def parseArgs(self, alias, nickname=None):
        BasedirOptions.parseArgs(self)
        self.alias = alias
        self.nickname = nickname
        node_url_file = os.path.join(self['node-directory'], "node.url")
        self['node-url'] = open(node_url_file, "r").read().strip()
        aliases = get_aliases(self['node-directory'])
        self.aliases = aliases

def diminish_readonly(write_cap, node_url):
    """
    given a write cap and a node url I will return the corresponding readcap
    or I'll return None on failure
    """
    list_options = ListOptions()
    list_options.where = u"%s" % (write_cap,)
    list_options["json"] = True
    list_options.aliases = {}
    list_options.stdin = StringIO("")
    list_options.stdout = StringIO()
    list_options.stderr = StringIO()
    list_options['node-url'] = node_url

    rc = tahoe_ls.list(list_options)
    if rc != 0:
        return None

    ls_json = list_options.stdout.getvalue()
    readonly_cap = json.loads(ls_json)[1][u"ro_uri"]
    return readonly_cap

def invite(options):
    from allmydata.scripts import tahoe_mkdir
    mkdir_options = MakeDirectoryOptions()
    mkdir_options.where = None
    mkdir_options.stdout = options.stdout
    mkdir_options.stdin = options.stdin
    mkdir_options.stderr = options.stderr
    mkdir_options['node-url'] = options['node-url']
    mkdir_options.aliases = options.aliases
    mkdir_options['node-directory'] = options['node-directory']

    rc = tahoe_mkdir.mkdir(mkdir_options)
    if rc != 0:
        # XXX failure
        print "tahoe mkdir FAIL"
        return rc
    dmd_write_cap = mkdir_options.stdout.getvalue().strip()
    dmd_readonly_cap = diminish_readonly(dmd_write_cap, options["node-url"])
    if dmd_readonly_cap is None:
        # XXX failure
        print "failure to diminish dmd write cap"
        return -1

    magic_write_cap = get_aliases(options["node-directory"])[options.alias]
    magic_readonly_cap = diminish_readonly(magic_write_cap, options["node-url"])

    # tahoe ln CLIENT_READCAP COLLECTIVE_WRITECAP/NICKNAME
    ln_options = LnOptions()
    ln_options["node-url"] = options["node-url"]
    ln_options.from_file = dmd_readonly_cap
    ln_options.to_file = "%s/%s" % (magic_write_cap, options.nickname)
    ln_options.aliases = options.aliases
    ln_options.stdin = StringIO("")
    ln_options.stdout = StringIO()
    ln_options.stderr = StringIO()
    rc = tahoe_mv.mv(ln_options, mode="link")
    if rc != 0:
        # XXX failure
        print "tahoe ln FAIL"
        return -1
    print "\ninvite code:\n%s-%s\n" % (magic_readonly_cap, dmd_write_cap)
    return rc

class JoinOptions(BasedirOptions):
    pass

def join(options):
    pass

class MagicFolderCommand(BaseOptions):
    subCommands = [
        ["create", None, CreateOptions, "Create a Magic-Folder."],
        ["invite", None, InviteOptions, "Invite someone to a Magic-Folder."],
        ["join", None, JoinOptions, "Join a Magic-Folder."],
    ]
    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a subcommand")
    def getSynopsis(self):
        return "Usage: tahoe [global-options] magic SUBCOMMAND"
    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += """\
Please run e.g. 'tahoe magic-folder create --help' for more details on each
subcommand.
"""
        return t

subDispatch = {
    "create": create,
    "invite": invite,
    "join": join,
}

def do_magic_folder(options):
    so = options.subOptions
    so.stdout = options.stdout
    so.stderr = options.stderr
    f = subDispatch[options.subCommand]
    return f(so)

subCommands = [
    ["magic-folder", None, MagicFolderCommand, "magic-folder subcommands: use 'tahoe magic-folder' for a list."],
]

dispatch = {
    "magic-folder": do_magic_folder,
}
