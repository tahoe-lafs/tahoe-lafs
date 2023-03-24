"""
Ported to Python 3.
"""

from twisted.python import usage
from twisted.internet import defer, reactor

from allmydata.util.encodingutil import argv_to_abspath
from allmydata.util import jsonbytes as json
from allmydata.scripts.common import get_default_nodedir, get_introducer_furl
from allmydata.scripts.types_ import SubCommands
from allmydata.client import read_config


class InviteOptions(usage.Options):
    synopsis = "[options] <nickname>"
    description = "Create a client-only Tahoe-LAFS node (no storage server)."

    optParameters = [
        ("shares-needed", None, None, "How many shares are needed to reconstruct files from this node"),
        ("shares-happy", None, None, "Distinct storage servers new node will upload shares to"),
        ("shares-total", None, None, "Total number of shares new node will upload"),
    ]

    def parseArgs(self, *args):
        if len(args) != 1:
            raise usage.UsageError(
                "Provide a single argument: the new node's nickname"
            )
        self['nick'] = args[0].strip()


@defer.inlineCallbacks
def _send_config_via_wormhole(options, config):
    out = options.stdout
    err = options.stderr
    relay_url = options.parent['wormhole-server']
    print("Connecting to '{}'...".format(relay_url), file=out)
    wh = options.parent.wormhole.create(
        appid=options.parent['wormhole-invite-appid'],
        relay_url=relay_url,
        reactor=reactor,
    )
    yield wh.get_welcome()
    print("Connected to wormhole server", file=out)

    # must call allocate_code before get_code will ever succeed
    wh.allocate_code()
    code = yield wh.get_code()
    print("Invite Code for client: {}".format(code), file=out)

    wh.send_message(json.dumps_bytes({
        u"abilities": {
            u"server-v1": {},
        }
    }))

    client_intro = yield wh.get_message()
    print("  received client introduction", file=out)
    client_intro = json.loads(client_intro)
    if not u'abilities' in client_intro:
        print("No 'abilities' from client", file=err)
        defer.returnValue(1)
    if not u'client-v1' in client_intro[u'abilities']:
        print("No 'client-v1' in abilities from client", file=err)
        defer.returnValue(1)

    print("  transmitting configuration", file=out)
    wh.send_message(json.dumps_bytes(config))
    yield wh.close()


@defer.inlineCallbacks
def invite(options):
    if options.parent['node-directory']:
        basedir = argv_to_abspath(options.parent['node-directory'])
    else:
        basedir = get_default_nodedir()
    config = read_config(basedir, u"")
    out = options.stdout
    err = options.stderr

    try:
        introducer_furl = get_introducer_furl(basedir, config)
    except Exception as e:
        print("Can't find introducer FURL for node '{}': {}".format(basedir, str(e)), file=err)
        raise SystemExit(1)

    nick = options['nick']

    remote_config = {
        "shares-needed": options["shares-needed"] or config.get_config('client', 'shares.needed'),
        "shares-total": options["shares-total"] or config.get_config('client', 'shares.total'),
        "shares-happy": options["shares-happy"] or config.get_config('client', 'shares.happy'),
        "nickname": nick,
        "introducer": introducer_furl,
    }

    yield _send_config_via_wormhole(options, remote_config)
    print("Completed successfully", file=out)


subCommands : SubCommands = [
    ("invite", None, InviteOptions,
     "Invite a new node to this grid"),
]

dispatch = {
    "invite": invite,
}
