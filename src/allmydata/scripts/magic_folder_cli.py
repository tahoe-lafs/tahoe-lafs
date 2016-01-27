
import os
import urllib
from sys import stderr
from types import NoneType
from cStringIO import StringIO
from datetime import datetime

import humanize
import simplejson  # XXX why not built-in json lib?

from twisted.python import usage

from allmydata.util.assertutil import precondition

from .common import BaseOptions, BasedirOptions, get_aliases
from .cli import MakeDirectoryOptions, LnOptions, CreateAliasOptions
import tahoe_mv
from allmydata.util.encodingutil import argv_to_abspath, argv_to_unicode, to_str, \
    quote_local_unicode_path
from allmydata.scripts.common_http import do_http, format_http_success, \
    format_http_error, BadResponse
from allmydata.util import fileutil
from allmydata.util import configutil
from allmydata import uri


INVITE_SEPARATOR = "+"

class CreateOptions(BasedirOptions):
    nickname = None
    local_dir = None
    synopsis = "MAGIC_ALIAS: [NICKNAME LOCAL_DIR]"
    def parseArgs(self, alias, nickname=None, local_dir=None):
        BasedirOptions.parseArgs(self)
        alias = argv_to_unicode(alias)
        if not alias.endswith(u':'):
            raise usage.UsageError("An alias must end with a ':' character.")
        self.alias = alias[:-1]
        self.nickname = None if nickname is None else argv_to_unicode(nickname)

        # Expand the path relative to the current directory of the CLI command, not the node.
        self.local_dir = None if local_dir is None else argv_to_abspath(local_dir, long_path=False)

        if self.nickname and not self.local_dir:
            raise usage.UsageError("If NICKNAME is specified then LOCAL_DIR must also be specified.")
        node_url_file = os.path.join(self['node-directory'], u"node.url")
        self['node-url'] = fileutil.read(node_url_file).strip()

def _delegate_options(source_options, target_options):
    target_options.aliases = get_aliases(source_options['node-directory'])
    target_options["node-url"] = source_options["node-url"]
    target_options["node-directory"] = source_options["node-directory"]
    target_options.stdin = StringIO("")
    target_options.stdout = StringIO()
    target_options.stderr = StringIO()
    return target_options

def create(options):
    precondition(isinstance(options.alias, unicode), alias=options.alias)
    precondition(isinstance(options.nickname, (unicode, NoneType)), nickname=options.nickname)
    precondition(isinstance(options.local_dir, (unicode, NoneType)), local_dir=options.local_dir)

    from allmydata.scripts import tahoe_add_alias
    create_alias_options = _delegate_options(options, CreateAliasOptions())
    create_alias_options.alias = options.alias

    rc = tahoe_add_alias.create_alias(create_alias_options)
    if rc != 0:
        print >>options.stderr, create_alias_options.stderr.getvalue()
        return rc
    print >>options.stdout, create_alias_options.stdout.getvalue()

    if options.nickname is not None:
        invite_options = _delegate_options(options, InviteOptions())
        invite_options.alias = options.alias
        invite_options.nickname = options.nickname
        rc = invite(invite_options)
        if rc != 0:
            print >>options.stderr, "magic-folder: failed to invite after create\n"
            print >>options.stderr, invite_options.stderr.getvalue()
            return rc
        invite_code = invite_options.stdout.getvalue().strip()
        join_options = _delegate_options(options, JoinOptions())
        join_options.local_dir = options.local_dir
        join_options.invite_code = invite_code
        rc = join(join_options)
        if rc != 0:
            print >>options.stderr, "magic-folder: failed to join after create\n"
            print >>options.stderr, join_options.stderr.getvalue()
            return rc
    return 0

class InviteOptions(BasedirOptions):
    nickname = None
    synopsis = "MAGIC_ALIAS: NICKNAME"
    stdin = StringIO("")
    def parseArgs(self, alias, nickname=None):
        BasedirOptions.parseArgs(self)
        alias = argv_to_unicode(alias)
        if not alias.endswith(u':'):
            raise usage.UsageError("An alias must end with a ':' character.")
        self.alias = alias[:-1]
        self.nickname = argv_to_unicode(nickname)
        node_url_file = os.path.join(self['node-directory'], u"node.url")
        self['node-url'] = open(node_url_file, "r").read().strip()
        aliases = get_aliases(self['node-directory'])
        self.aliases = aliases

def invite(options):
    precondition(isinstance(options.alias, unicode), alias=options.alias)
    precondition(isinstance(options.nickname, unicode), nickname=options.nickname)

    from allmydata.scripts import tahoe_mkdir
    mkdir_options = _delegate_options(options, MakeDirectoryOptions())
    mkdir_options.where = None

    rc = tahoe_mkdir.mkdir(mkdir_options)
    if rc != 0:
        print >>options.stderr, "magic-folder: failed to mkdir\n"
        return rc

    # FIXME this assumes caps are ASCII.
    dmd_write_cap = mkdir_options.stdout.getvalue().strip()
    dmd_readonly_cap = uri.from_string(dmd_write_cap).get_readonly().to_string()
    if dmd_readonly_cap is None:
        print >>options.stderr, "magic-folder: failed to diminish dmd write cap\n"
        return 1

    magic_write_cap = get_aliases(options["node-directory"])[options.alias]
    magic_readonly_cap = uri.from_string(magic_write_cap).get_readonly().to_string()

    # tahoe ln CLIENT_READCAP COLLECTIVE_WRITECAP/NICKNAME
    ln_options = _delegate_options(options, LnOptions())
    ln_options.from_file = unicode(dmd_readonly_cap, 'utf-8')
    ln_options.to_file = u"%s/%s" % (unicode(magic_write_cap, 'utf-8'), options.nickname)
    rc = tahoe_mv.mv(ln_options, mode="link")
    if rc != 0:
        print >>options.stderr, "magic-folder: failed to create link\n"
        print >>options.stderr, ln_options.stderr.getvalue()
        return rc

    # FIXME: this assumes caps are ASCII.
    print >>options.stdout, "%s%s%s" % (magic_readonly_cap, INVITE_SEPARATOR, dmd_write_cap)
    return 0

class JoinOptions(BasedirOptions):
    synopsis = "INVITE_CODE LOCAL_DIR"
    dmd_write_cap = ""
    magic_readonly_cap = ""
    def parseArgs(self, invite_code, local_dir):
        BasedirOptions.parseArgs(self)

        # Expand the path relative to the current directory of the CLI command, not the node.
        self.local_dir = None if local_dir is None else argv_to_abspath(local_dir, long_path=False)
        self.invite_code = to_str(argv_to_unicode(invite_code))

def join(options):
    fields = options.invite_code.split(INVITE_SEPARATOR)
    if len(fields) != 2:
        raise usage.UsageError("Invalid invite code.")
    magic_readonly_cap, dmd_write_cap = fields

    dmd_cap_file = os.path.join(options["node-directory"], u"private", u"magic_folder_dircap")
    collective_readcap_file = os.path.join(options["node-directory"], u"private", u"collective_dircap")
    magic_folder_db_file = os.path.join(options["node-directory"], u"private", u"magicfolderdb.sqlite")

    if os.path.exists(dmd_cap_file) or os.path.exists(collective_readcap_file) or os.path.exists(magic_folder_db_file):
        print >>options.stderr, ("\nThis client has already joined a magic folder."
                                 "\nUse the 'tahoe magic-folder leave' command first.\n")
        return 1

    fileutil.write(dmd_cap_file, dmd_write_cap)
    fileutil.write(collective_readcap_file, magic_readonly_cap)

    config = configutil.get_config(os.path.join(options["node-directory"], u"tahoe.cfg"))
    configutil.set_config(config, "magic_folder", "enabled", "True")
    configutil.set_config(config, "magic_folder", "local.directory", options.local_dir.encode('utf-8'))
    configutil.write_config(os.path.join(options["node-directory"], u"tahoe.cfg"), config)
    return 0

class LeaveOptions(BasedirOptions):
    synopsis = ""
    def parseArgs(self):
        BasedirOptions.parseArgs(self)

def leave(options):
    from ConfigParser import SafeConfigParser

    dmd_cap_file = os.path.join(options["node-directory"], u"private", u"magic_folder_dircap")
    collective_readcap_file = os.path.join(options["node-directory"], u"private", u"collective_dircap")
    magic_folder_db_file = os.path.join(options["node-directory"], u"private", u"magicfolderdb.sqlite")

    parser = SafeConfigParser()
    parser.read(os.path.join(options["node-directory"], u"tahoe.cfg"))
    parser.remove_section("magic_folder")
    f = open(os.path.join(options["node-directory"], u"tahoe.cfg"), "w")
    parser.write(f)
    f.close()

    for f in [dmd_cap_file, collective_readcap_file, magic_folder_db_file]:
        try:
            fileutil.remove(f)
        except Exception as e:
            print >>options.stderr, ("Warning: unable to remove %s due to %s: %s"
                % (quote_local_unicode_path(f), e.__class__.__name__, str(e)))

class StatusOptions(BasedirOptions):
    nickname = None
    synopsis = ""
    stdin = StringIO("")

    def parseArgs(self):
        BasedirOptions.parseArgs(self)
        node_url_file = os.path.join(self['node-directory'], u"node.url")
        with open(node_url_file, "r") as f:
            self['node-url'] = f.read().strip()


def _get_json_for_fragment(options, fragment, method='GET'):
    nodeurl = options['node-url']
    if nodeurl.endswith('/'):
        nodeurl = nodeurl[:-1]

    url = u'%s/%s' % (nodeurl, fragment)
    resp = do_http(method, url)
    if isinstance(resp, BadResponse):
        # specifically NOT using format_http_error() here because the
        # URL is pretty sensitive (we're doing /uri/<key>).
        raise RuntimeError(
            "Failed to get json from '%s': %s" % (nodeurl, resp.error)
        )

    data = resp.read()
    parsed = simplejson.loads(data)
    if not parsed:
        raise RuntimeError("No data from '%s'" % (nodeurl,))
    return parsed


def _get_json_for_cap(options, cap):
    return _get_json_for_fragment(
        options,
        'uri/%s?t=json' % urllib.quote(cap),
    )

def _print_item_status(item, now, longest):
    paddedname = (' ' * (longest - len(item['path']))) + item['path']
    if 'failure_at' in item:
        ts = datetime.fromtimestamp(item['started_at'])
        prog = 'Failed %s (%s)' % (humanize.naturaltime(now - ts), ts)
    elif item['percent_done'] < 100.0:
        if 'started_at' not in item:
            prog = 'not yet started'
        else:
            so_far = now - datetime.fromtimestamp(item['started_at'])
            if so_far.seconds > 0.0:
                rate = item['percent_done'] / so_far.seconds
                if rate != 0:
                    time_left = (100.0 - item['percent_done']) / rate
                    prog = '%2.1f%% done, around %s left' % (
                        item['percent_done'],
                        humanize.naturaldelta(time_left),
                    )
                else:
                    time_left = None
                    prog = '%2.1f%% done' % (item['percent_done'],)
            else:
                prog = 'just started'
    else:
        prog = ''
        for verb in ['finished', 'started', 'queued']:
            keyname = verb + '_at'
            if keyname in item:
                when = datetime.fromtimestamp(item[keyname])
                prog = '%s %s' % (verb, humanize.naturaltime(now - when))
                break

    print "  %s: %s" % (paddedname, prog)

def status(options):
    nodedir = options["node-directory"]
    with open(os.path.join(nodedir, u"private", u"magic_folder_dircap")) as f:
        dmd_cap = f.read().strip()
    with open(os.path.join(nodedir, u"private", u"collective_dircap")) as f:
        collective_readcap = f.read().strip()

    try:
        captype, dmd = _get_json_for_cap(options, dmd_cap)
        if captype != 'dirnode':
            print >>stderr, "magic_folder_dircap isn't a directory capability"
            return 2
    except RuntimeError as e:
        print >>stderr, str(e)
        return 1

    now = datetime.now()

    print "Local files:"
    for (name, child) in dmd['children'].items():
        captype, meta = child
        status = 'good'
        size = meta['size']
        created = datetime.fromtimestamp(meta['metadata']['tahoe']['linkcrtime'])
        version = meta['metadata']['version']
        nice_size = humanize.naturalsize(size)
        nice_created = humanize.naturaltime(now - created)
        if captype != 'filenode':
            print "%20s: error, should be a filecap" % name
            continue
        print "  %s (%s): %s, version=%s, created %s" % (name, nice_size, status, version, nice_created)

    captype, collective = _get_json_for_cap(options, collective_readcap)
    print
    print "Remote files:"
    for (name, data) in collective['children'].items():
        if data[0] != 'dirnode':
            print "Error: '%s': expected a dirnode, not '%s'" % (name, data[0])
        print "  %s's remote:" % name
        dmd = _get_json_for_cap(options, data[1]['ro_uri'])
        if dmd[0] != 'dirnode':
            print "Error: should be a dirnode"
            continue
        for (n, d) in dmd[1]['children'].items():
            if d[0] != 'filenode':
                print "Error: expected '%s' to be a filenode." % (n,)

            meta = d[1]
            status = 'good'
            size = meta['size']
            created = datetime.fromtimestamp(meta['metadata']['tahoe']['linkcrtime'])
            version = meta['metadata']['version']
            nice_size = humanize.naturalsize(size)
            nice_created = humanize.naturaltime(now - created)
            print "    %s (%s): %s, version=%s, created %s" % (n, nice_size, status, version, nice_created)

    with open(os.path.join(nodedir, u'private', u'api_auth_token'), 'rb') as f:
        token = f.read()
    magicdata = _get_json_for_fragment(
        options,
        'magic_folder?t=json&token=' + token,
        method='POST',
    )
    if len(magicdata):
        uploads = [item for item in magicdata if item['kind'] == 'upload']
        downloads = [item for item in magicdata if item['kind'] == 'download']
        longest = max([len(item['path']) for item in magicdata])

        if True: # maybe --show-completed option or something?
            uploads = [item for item in uploads if item['status'] != 'success']
            downloads = [item for item in downloads if item['status'] != 'success']

        if len(uploads):
            print
            print "Uploads:"
            for item in uploads:
                _print_item_status(item, now, longest)

        if len(downloads):
            print
            print "Downloads:"
            for item in downloads:
                _print_item_status(item, now, longest)

        for item in magicdata:
            if item['status'] == 'failure':
                print "Failed:", item

    return 0


class MagicFolderCommand(BaseOptions):
    subCommands = [
        ["create", None, CreateOptions, "Create a Magic Folder."],
        ["invite", None, InviteOptions, "Invite someone to a Magic Folder."],
        ["join", None, JoinOptions, "Join a Magic Folder."],
        ["leave", None, LeaveOptions, "Leave a Magic Folder."],
        ["status", None, StatusOptions, "Display stutus of uploads/downloads."],
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
    "leave": leave,
    "status": status,
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
