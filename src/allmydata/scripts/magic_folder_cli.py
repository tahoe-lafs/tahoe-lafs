
import os
from cStringIO import StringIO
from twisted.python import usage

from .common import BaseOptions, BasedirOptions, get_aliases
from .cli import MakeDirectoryOptions, LnOptions
import tahoe_mv
from allmydata.util import fileutil
from allmydata import uri

INVITE_SEPARATOR = "~"

class CreateOptions(BasedirOptions):
    nickname = None
    localdir = None
    synopsis = "MAGIC_ALIAS: [NICKNAME LOCALDIR]"
    def parseArgs(self, alias, nickname=None, localdir=None):
        BasedirOptions.parseArgs(self)
        if not alias.endswith(':'):
            raise usage.UsageError("An alias must end with a ':' character.")
        self.alias = alias[:-1]
        self.nickname = nickname
        self.localdir = localdir
        if self.nickname and not self.localdir:
            raise usage.UsageError("If NICKNAME is specified then LOCALDIR must also be specified.")
        node_url_file = os.path.join(self['node-directory'], "node.url")
        self['node-url'] = fileutil.read(node_url_file).strip()

def create(options):
    from allmydata.scripts import tahoe_add_alias
    rc = tahoe_add_alias.create_alias(options)

    if options.nickname is not None:
        invite_options = InviteOptions()
        invite_options.aliases = get_aliases(options['node-directory'])
        invite_options["node-url"] = options["node-url"]
        invite_options["node-directory"] = options["node-directory"]
        invite_options.alias = options.alias
        invite_options.nickname = options.nickname
        invite_options.stdin = StringIO("")
        invite_options.stdout = StringIO()
        invite_options.stderr = StringIO()
        rc = invite(invite_options)
        if rc != 0:
            print >>options.stderr, "magic-folder: failed to invite after create\n"
            return -1
        invite_code = invite_options.stdout.getvalue().strip()

        join_options = JoinOptions()
        join_options.alias = options.alias
        join_options.aliases = get_aliases(options['node-directory'])
        join_options["node-url"] = options["node-url"]
        join_options["node-directory"] = options["node-directory"]
        join_options.invite_code = invite_code
        fields = invite_code.split(INVITE_SEPARATOR)
        if len(fields) != 2:
            raise usage.UsageError("Invalid invite code.")
        join_options.magic_readonly_cap, join_options.dmd_write_cap = fields
        join_options.local_dir = options.localdir
        rc = join(join_options)
        if rc != 0:
            print >>options.stderr, "magic-folder: failed to invite after create\n"
            return -1
    return 0

class InviteOptions(BasedirOptions):
    nickname = None
    synopsis = "MAGIC_ALIAS: NICKNAME"
    stdin = StringIO("")
    def parseArgs(self, alias, nickname=None):
        BasedirOptions.parseArgs(self)
        if not alias.endswith(':'):
            raise usage.UsageError("An alias must end with a ':' character.")
        self.alias = alias[:-1]
        self.nickname = nickname
        node_url_file = os.path.join(self['node-directory'], "node.url")
        self['node-url'] = open(node_url_file, "r").read().strip()
        aliases = get_aliases(self['node-directory'])
        self.aliases = aliases

def invite(options):
    from allmydata.scripts import tahoe_mkdir
    mkdir_options = MakeDirectoryOptions()
    mkdir_options.where = None
    mkdir_options.stdin = StringIO("")
    mkdir_options.stdout = StringIO()
    mkdir_options.stderr = StringIO()
    mkdir_options.aliases = options.aliases
    mkdir_options['node-url'] = options['node-url']
    mkdir_options['node-directory'] = options['node-directory']

    rc = tahoe_mkdir.mkdir(mkdir_options)
    if rc != 0:
        # XXX failure
        print >>options.stderr, "magic-folder: failed to mkdir\n"
        return -1
    dmd_write_cap = mkdir_options.stdout.getvalue().strip()
    dmd_readonly_cap = unicode(uri.from_string(dmd_write_cap).get_readonly().to_string(), 'utf-8')
    if dmd_readonly_cap is None:
        # XXX failure
        print >>options.stderr, "magic-folder: failed to diminish dmd write cap\n"
        return -1

    magic_write_cap = get_aliases(options["node-directory"])[options.alias]
    magic_readonly_cap = unicode(uri.from_string(magic_write_cap).get_readonly().to_string(), 'utf-8')
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
        print >>options.stderr, "magic-folder: failed to create link\n"
        return -1

    print >>options.stdout, "%s%s%s" % (magic_readonly_cap, INVITE_SEPARATOR, dmd_write_cap)
    return 0

class JoinOptions(BasedirOptions):
    synopsis = "INVITE_CODE LOCAL_DIR"
    dmd_write_cap = ""
    magic_readonly_cap = ""
    def parseArgs(self, invite_code, local_dir):
        BasedirOptions.parseArgs(self)
        self.local_dir = local_dir
        fields = invite_code.split(INVITE_SEPARATOR)
        if len(fields) != 2:
            raise usage.UsageError("Invalid invite code.")
        self.magic_readonly_cap, self.dmd_write_cap = fields

def join(options):
    dmd_cap_file = os.path.join(options["node-directory"], "private/magic_folder_dircap")
    collective_readcap_file = os.path.join(options["node-directory"], "private/collective_dircap")

    fileutil.write(dmd_cap_file, options.dmd_write_cap)
    fileutil.write(collective_readcap_file, options.magic_readonly_cap)
    fileutil.write(os.path.join(options["node-directory"], "tahoe.cfg"),
                   "[magic_folder]\nenabled = True\nlocal.directory = %s\n"
                   % (options.local_dir.encode('utf-8'),), mode="ab")
    return 0

class MagicFolderCommand(BaseOptions):
    subCommands = [
        ["create", None, CreateOptions, "Create a Magic Folder."],
        ["invite", None, InviteOptions, "Invite someone to a Magic Folder."],
        ["join", None, JoinOptions, "Join a Magic Folder."],
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
    ["magic-folder", None, MagicFolderCommand,
     "Magic Folder subcommands: use 'tahoe magic-folder' for a list."],
]

dispatch = {
    "magic-folder": do_magic_folder,
}
