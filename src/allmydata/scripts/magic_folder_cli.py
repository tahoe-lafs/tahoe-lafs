
import os

from twisted.python import usage, failure
from allmydata.scripts.common import BaseOptions
from .common import BaseOptions, BasedirOptions, get_aliases
from .cli import MakeDirectoryOptions

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
    def parseArgs(self, alias, nickname=None):
        BasedirOptions.parseArgs(self)
        print "InviteOptions parseArgs() alias %s nickname %s" % (alias, nickname,)
        self.alias = alias
        self.nickname = nickname
        node_url_file = os.path.join(self['node-directory'], "node.url")
        self['node-url'] = open(node_url_file, "r").read().strip()

        aliases = get_aliases(self['node-directory'])
        print "ALIASES %s" % (aliases,)
        self.aliases = aliases

def invite(options):
    from allmydata.scripts import tahoe_mkdir
    mkdirOptions = MakeDirectoryOptions()
    mkdirOptions.where = options.nickname
    mkdirOptions.stdout = options.stdout
    mkdirOptions.stdin = options.stdin
    mkdirOptions.stderr = options.stderr
    mkdirOptions['node-url'] = options['node-url']
    mkdirOptions.aliases = options.aliases
    mkdirOptions['node-directory'] = options['node-directory']
    rc = tahoe_mkdir.mkdir(mkdirOptions)
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
