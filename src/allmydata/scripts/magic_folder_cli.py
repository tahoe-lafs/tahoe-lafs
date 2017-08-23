
import os
import urllib
from types import NoneType
from cStringIO import StringIO
from datetime import datetime
import json


from twisted.python import usage

from allmydata.util.assertutil import precondition

from .common import BaseOptions, BasedirOptions, get_aliases
from .cli import MakeDirectoryOptions, LnOptions, CreateAliasOptions
import tahoe_mv
from allmydata.util.encodingutil import argv_to_abspath, argv_to_unicode, to_str, \
    quote_local_unicode_path
from allmydata.scripts.common_http import do_http, BadResponse
from allmydata.util import fileutil
from allmydata import uri
from allmydata.util.abbreviate import abbreviate_space, abbreviate_time
from allmydata.frontends.magic_folder import load_magic_folders
from allmydata.frontends.magic_folder import save_magic_folders
from allmydata.frontends.magic_folder import maybe_upgrade_magic_folders


INVITE_SEPARATOR = "+"

class CreateOptions(BasedirOptions):
    nickname = None  # NOTE: *not* the "name of this magic-folder"
    local_dir = None
    synopsis = "MAGIC_ALIAS: [NICKNAME LOCAL_DIR]"
    optParameters = [
        ("poll-interval", "p", "60", "How often to ask for updates"),
        ("name", "n", "default", "The name of this magic-folder"),
    ]
    description = (
        "Create a new magic-folder. If you specify NICKNAME and "
        "LOCAL_DIR, this client will also be invited and join "
        "using the given nickname. A new alias (see 'tahoe list-aliases') "
        "will be added with the master folder's writecap."
    )

    def parseArgs(self, alias, nickname=None, local_dir=None):
        BasedirOptions.parseArgs(self)
        alias = argv_to_unicode(alias)
        if not alias.endswith(u':'):
            raise usage.UsageError("An alias must end with a ':' character.")
        self.alias = alias[:-1]
        self.nickname = None if nickname is None else argv_to_unicode(nickname)
        try:
            if int(self['poll-interval']) <= 0:
                raise ValueError("should be positive")
        except ValueError:
            raise usage.UsageError(
                "--poll-interval must be a positive integer"
            )

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
    target_options["name"] = source_options["name"]
    target_options.stdin = StringIO("")
    target_options.stdout = StringIO()
    target_options.stderr = StringIO()
    return target_options

def create(options):
    precondition(isinstance(options.alias, unicode), alias=options.alias)
    precondition(isinstance(options.nickname, (unicode, NoneType)), nickname=options.nickname)
    precondition(isinstance(options.local_dir, (unicode, NoneType)), local_dir=options.local_dir)

    # make sure we don't already have a magic-folder with this name before we create the alias
    maybe_upgrade_magic_folders(options["node-directory"])
    folders = load_magic_folders(options["node-directory"])
    if options['name'] in folders:
        print >>options.stderr, "Already have a magic-folder named '{}'".format(options['name'])
        return 1

    # create an alias; this basically just remembers the cap for the
    # master directory
    from allmydata.scripts import tahoe_add_alias
    create_alias_options = _delegate_options(options, CreateAliasOptions())
    create_alias_options.alias = options.alias

    rc = tahoe_add_alias.create_alias(create_alias_options)
    if rc != 0:
        print >>options.stderr, create_alias_options.stderr.getvalue()
        return rc
    print >>options.stdout, create_alias_options.stdout.getvalue()

    if options.nickname is not None:
        print >>options.stdout, u"Inviting myself as client '{}':".format(options.nickname)
        invite_options = _delegate_options(options, InviteOptions())
        invite_options.alias = options.alias
        invite_options.nickname = options.nickname
        invite_options['name'] = options['name']
        rc = invite(invite_options)
        if rc != 0:
            print >>options.stderr, u"magic-folder: failed to invite after create\n"
            print >>options.stderr, invite_options.stderr.getvalue()
            return rc
        invite_code = invite_options.stdout.getvalue().strip()
        print >>options.stdout, u"  created invite code"
        join_options = _delegate_options(options, JoinOptions())
        join_options['poll-interval'] = options['poll-interval']
        join_options.nickname = options.nickname
        join_options.local_dir = options.local_dir
        join_options.invite_code = invite_code
        rc = join(join_options)
        if rc != 0:
            print >>options.stderr, u"magic-folder: failed to join after create\n"
            print >>options.stderr, join_options.stderr.getvalue()
            return rc
        print >>options.stdout, u"  joined new magic-folder"
        print >>options.stdout, (
            u"Successfully created magic-folder '{}' with alias '{}:' "
            u"and client '{}'\nYou must re-start your node before the "
            u"magic-folder will be active."
        ).format(options['name'], options.alias, options.nickname)
    return 0


class ListOptions(BasedirOptions):
    description = (
        "List all magic-folders this client has joined"
    )
    optFlags = [
        ("json", "", "Produce JSON output")
    ]


def list_(options):
    folders = load_magic_folders(options["node-directory"])
    if options["json"]:
        _list_json(options, folders)
        return 0
    _list_human(options, folders)
    return 0


def _list_json(options, folders):
    """
    List our magic-folders using JSON
    """
    info = dict()
    for name, details in folders.items():
        info[name] = {
            u"directory": details["directory"],
        }
    print >>options.stdout, json.dumps(info)
    return 0


def _list_human(options, folders):
    """
    List our magic-folders for a human user
    """
    if folders:
        print >>options.stdout, "This client has the following magic-folders:"
        biggest = max([len(nm) for nm in folders.keys()])
        fmt = "  {:>%d}: {}" % (biggest, )
        for name, details in folders.items():
            print >>options.stdout, fmt.format(name, details["directory"])
    else:
        print >>options.stdout, "No magic-folders"


class InviteOptions(BasedirOptions):
    nickname = None
    synopsis = "MAGIC_ALIAS: NICKNAME"
    stdin = StringIO("")
    optParameters = [
        ("name", "n", "default", "The name of this magic-folder"),
    ]
    description = (
        "Invite a new participant to a given magic-folder. The resulting "
        "invite-code that is printed is secret information and MUST be "
        "transmitted securely to the invitee."
    )

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
    optParameters = [
        ("poll-interval", "p", "60", "How often to ask for updates"),
        ("name", "n", "default", "Name of the magic-folder"),
    ]

    def parseArgs(self, invite_code, local_dir):
        BasedirOptions.parseArgs(self)

        try:
            if int(self['poll-interval']) <= 0:
                raise ValueError("should be positive")
        except ValueError:
            raise usage.UsageError(
                "--poll-interval must be a positive integer"
            )
        # Expand the path relative to the current directory of the CLI command, not the node.
        self.local_dir = None if local_dir is None else argv_to_abspath(local_dir, long_path=False)
        self.invite_code = to_str(argv_to_unicode(invite_code))

def join(options):
    fields = options.invite_code.split(INVITE_SEPARATOR)
    if len(fields) != 2:
        raise usage.UsageError("Invalid invite code.")
    magic_readonly_cap, dmd_write_cap = fields

    maybe_upgrade_magic_folders(options["node-directory"])
    existing_folders = load_magic_folders(options["node-directory"])

    if options['name'] in existing_folders:
        print >>options.stderr, "This client already has a magic-folder named '{}'".format(options['name'])
        return 1

    db_fname = os.path.join(
        options["node-directory"],
        u"private",
        u"magicfolder_{}.sqlite".format(options['name']),
    )
    if os.path.exists(db_fname):
        print >>options.stderr, "Database '{}' already exists; not overwriting".format(db_fname)
        return 1

    folder = {
        u"directory": options.local_dir.encode('utf-8'),
        u"collective_dircap": magic_readonly_cap,
        u"upload_dircap": dmd_write_cap,
        u"poll_interval": options["poll-interval"],
    }
    existing_folders[options["name"]] = folder

    save_magic_folders(options["node-directory"], existing_folders)
    return 0


class LeaveOptions(BasedirOptions):
    synopsis = "Remove a magic-folder and forget all state"
    optParameters = [
        ("name", "n", "default", "Name of magic-folder to leave"),
    ]


def leave(options):
    from ConfigParser import SafeConfigParser

    existing_folders = load_magic_folders(options["node-directory"])

    if not existing_folders:
        print >>options.stderr, "No magic-folders at all"
        return 1

    if options["name"] not in existing_folders:
        print >>options.stderr, "No such magic-folder '{}'".format(options["name"])
        return 1

    privdir = os.path.join(options["node-directory"], u"private")
    db_fname = os.path.join(privdir, u"magicfolder_{}.sqlite".format(options["name"]))

    # delete from YAML file and re-write it
    del existing_folders[options["name"]]
    save_magic_folders(options["node-directory"], existing_folders)

    # delete the database file
    try:
        fileutil.remove(db_fname)
    except Exception as e:
        print >>options.stderr, ("Warning: unable to remove %s due to %s: %s"
            % (quote_local_unicode_path(db_fname), e.__class__.__name__, str(e)))

    # if this was the last magic-folder, disable them entirely
    if not existing_folders:
        parser = SafeConfigParser()
        parser.read(os.path.join(options["node-directory"], u"tahoe.cfg"))
        parser.remove_section("magic_folder")
        with open(os.path.join(options["node-directory"], u"tahoe.cfg"), "w") as f:
            parser.write(f)

    return 0


class StatusOptions(BasedirOptions):
    synopsis = ""
    stdin = StringIO("")
    optParameters = [
        ("name", "n", "default", "Name for the magic-folder to show status"),
    ]

    def parseArgs(self):
        BasedirOptions.parseArgs(self)
        node_url_file = os.path.join(self['node-directory'], u"node.url")
        with open(node_url_file, "r") as f:
            self['node-url'] = f.read().strip()


def _get_json_for_fragment(options, fragment, method='GET', post_args=None):
    nodeurl = options['node-url']
    if nodeurl.endswith('/'):
        nodeurl = nodeurl[:-1]

    url = u'%s/%s' % (nodeurl, fragment)
    if method == 'POST':
        if post_args is None:
            raise ValueError("Must pass post_args= for POST method")
        body = urllib.urlencode(post_args)
    else:
        body = ''
        if post_args is not None:
            raise ValueError("post_args= only valid for POST method")
    resp = do_http(method, url, body=body)
    if isinstance(resp, BadResponse):
        # specifically NOT using format_http_error() here because the
        # URL is pretty sensitive (we're doing /uri/<key>).
        raise RuntimeError(
            "Failed to get json from '%s': %s" % (nodeurl, resp.error)
        )

    data = resp.read()
    parsed = json.loads(data)
    if parsed is None:
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
        prog = 'Failed %s (%s)' % (abbreviate_time(now - ts), ts)
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
                        abbreviate_time(time_left),
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
                prog = '%s %s' % (verb, abbreviate_time(now - when))
                break

    print "  %s: %s" % (paddedname, prog)


def status(options):
    nodedir = options["node-directory"]
    stdout, stderr = options.stdout, options.stderr
    magic_folders = load_magic_folders(os.path.join(options["node-directory"]))

    with open(os.path.join(nodedir, u'private', u'api_auth_token'), 'rb') as f:
        token = f.read()

    print >>stdout, "Magic-folder status for '{}':".format(options["name"])

    if options["name"] not in magic_folders:
        raise Exception(
            "No such magic-folder '{}'".format(options["name"])
        )

    dmd_cap = magic_folders[options["name"]]["upload_dircap"]
    collective_readcap = magic_folders[options["name"]]["collective_dircap"]

    # do *all* our data-retrievals first in case there's an error
    try:
        dmd_data = _get_json_for_cap(options, dmd_cap)
        remote_data = _get_json_for_cap(options, collective_readcap)
        magic_data = _get_json_for_fragment(
            options,
            'magic_folder?t=json',
            method='POST',
            post_args=dict(
                t='json',
                name=options["name"],
                token=token,
            )
        )
    except Exception as e:
        print >>stderr, "failed to retrieve data: %s" % str(e)
        return 2

    for d in [dmd_data, remote_data, magic_data]:
        if isinstance(d, dict) and 'error' in d:
            print >>stderr, "Error from server: %s" % d['error']
            print >>stderr, "This means we can't retrieve the remote shared directory."
            return 3

    captype, dmd = dmd_data
    if captype != 'dirnode':
        print >>stderr, "magic_folder_dircap isn't a directory capability"
        return 2

    now = datetime.now()

    print >>stdout, "Local files:"
    for (name, child) in dmd['children'].items():
        captype, meta = child
        status = 'good'
        size = meta['size']
        created = datetime.fromtimestamp(meta['metadata']['tahoe']['linkcrtime'])
        version = meta['metadata']['version']
        nice_size = abbreviate_space(size)
        nice_created = abbreviate_time(now - created)
        if captype != 'filenode':
            print >>stdout, "%20s: error, should be a filecap" % name
            continue
        print >>stdout, "  %s (%s): %s, version=%s, created %s" % (name, nice_size, status, version, nice_created)

    print >>stdout
    print >>stdout, "Remote files:"

    captype, collective = remote_data
    for (name, data) in collective['children'].items():
        if data[0] != 'dirnode':
            print >>stdout, "Error: '%s': expected a dirnode, not '%s'" % (name, data[0])
        print >>stdout, "  %s's remote:" % name
        dmd = _get_json_for_cap(options, data[1]['ro_uri'])
        if isinstance(dmd, dict) and 'error' in dmd:
            print >>stdout, "    Error: could not retrieve directory"
            continue
        if dmd[0] != 'dirnode':
            print >>stdout, "Error: should be a dirnode"
            continue
        for (n, d) in dmd[1]['children'].items():
            if d[0] != 'filenode':
                print >>stdout, "Error: expected '%s' to be a filenode." % (n,)

            meta = d[1]
            status = 'good'
            size = meta['size']
            created = datetime.fromtimestamp(meta['metadata']['tahoe']['linkcrtime'])
            version = meta['metadata']['version']
            nice_size = abbreviate_space(size)
            nice_created = abbreviate_time(now - created)
            print >>stdout, "    %s (%s): %s, version=%s, created %s" % (n, nice_size, status, version, nice_created)

    if len(magic_data):
        uploads = [item for item in magic_data if item['kind'] == 'upload']
        downloads = [item for item in magic_data if item['kind'] == 'download']
        longest = max([len(item['path']) for item in magic_data])

        if True: # maybe --show-completed option or something?
            uploads = [item for item in uploads if item['status'] != 'success']
            downloads = [item for item in downloads if item['status'] != 'success']

        if len(uploads):
            print
            print >>stdout, "Uploads:"
            for item in uploads:
                _print_item_status(item, now, longest)

        if len(downloads):
            print
            print >>stdout, "Downloads:"
            for item in downloads:
                _print_item_status(item, now, longest)

        for item in magic_data:
            if item['status'] == 'failure':
                print >>stdout, "Failed:", item

    return 0


class MagicFolderCommand(BaseOptions):
    subCommands = [
        ["create", None, CreateOptions, "Create a Magic Folder."],
        ["invite", None, InviteOptions, "Invite someone to a Magic Folder."],
        ["join", None, JoinOptions, "Join a Magic Folder."],
        ["leave", None, LeaveOptions, "Leave a Magic Folder."],
        ["status", None, StatusOptions, "Display status of uploads/downloads."],
        ["list", None, ListOptions, "List Magic Folders configured in this client."],
    ]
    optFlags = [
        ["debug", "d", "Print full stack-traces"],
    ]
    description = (
        "A magic-folder has an owner who controls the writecap "
        "containing a list of nicknames and readcaps. The owner can invite "
        "new participants. Every participant has the writecap for their "
        "own folder (the corresponding readcap is in the master folder). "
        "All clients download files from all other participants using the "
        "readcaps contained in the master magic-folder directory."
    )

    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a subcommand")
    def getSynopsis(self):
        return "Usage: tahoe [global-options] magic-folder"
    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += (
            "Please run e.g. 'tahoe magic-folder create --help' for more "
            "details on each subcommand.\n"
        )
        return t

subDispatch = {
    "create": create,
    "invite": invite,
    "join": join,
    "leave": leave,
    "status": status,
    "list": list_,
}

def do_magic_folder(options):
    so = options.subOptions
    so.stdout = options.stdout
    so.stderr = options.stderr
    f = subDispatch[options.subCommand]
    try:
        return f(so)
    except Exception as e:
        print >>options.stderr, "Error: %s" % (e,)
        if options['debug']:
            raise

subCommands = [
    ["magic-folder", None, MagicFolderCommand,
     "Magic Folder subcommands: use 'tahoe magic-folder' for a list."],
]

dispatch = {
    "magic-folder": do_magic_folder,
}
