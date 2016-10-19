from __future__ import print_function

import os
import urllib

import json

from .common import BaseOptions
from allmydata.scripts.common import get_default_nodedir
from allmydata.scripts.common_http import do_http, BadResponse
from allmydata.util.abbreviate import abbreviate_space, abbreviate_time
from allmydata.util.encodingutil import argv_to_abspath


def _get_json_for_fragment(options, fragment, method='GET', post_args=None):
    """
    returns the JSON for a particular URI-fragment (to which is
    pre-pended the node's URL)
    """
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

def pretty_progress(percent, size=10, ascii=False):
    """
    Displays a unicode or ascii based progress bar of a certain
    length. Should we just depend on a library instead?

    (Originally from txtorcon)
    """

    curr = int(percent / 100.0 * size)
    part = (percent / (100.0 / size)) - curr

    if ascii:
        part = int(part * 4)
        part = '.oO%'[part]
        block_chr = '#'

    else:
        block_chr = u'\u2588'
        # there are 8 unicode characters for vertical-bars/horiz-bars
        part = int(part * 8)

        # unicode 0x2581 -> 2589 are vertical bar chunks, like rainbarf uses
        # and following are narrow -> wider bars
        part = unichr(0x258f - part) # for smooth bar
        # part = unichr(0x2581 + part) # for neater-looking thing

    # hack for 100+ full so we don't print extra really-narrow/high bar
    if percent >= 100.0:
        part = ''
    curr = int(curr)
    return '%s%s%s' % ((block_chr * curr), part, (' ' * (size - curr - 1)))


def do_status(options):
    nodedir = options["node-directory"]
    with open(os.path.join(nodedir, u'private', u'api_auth_token'), 'r') as f:
        token = f.read().strip()
    with open(os.path.join(nodedir, u'node.url'), 'r') as f:
        options['node-url'] = f.read().strip()

    # do *all* our data-retrievals first in case there's an error
    try:
        status_data = _get_json_for_fragment(
            options,
            'status?t=json',
            method='POST',
            post_args=dict(
                t='json',
                token=token,
            )
        )
        statistics_data = _get_json_for_fragment(
            options,
            'statistics?t=json',
            method='POST',
            post_args=dict(
                t='json',
                token=token,
            )
        )

    except Exception as e:
        print(u"failed to retrieve data: %s" % str(e), file=options.stderr)
        return 2

    downloaded_bytes = statistics_data['counters'].get('downloader.bytes_downloaded', 0)
    downloaded_files = statistics_data['counters'].get('downloader.files_downloaded', 0)
    uploaded_bytes = statistics_data['counters'].get('uploader.bytes_uploaded', 0)
    uploaded_files = statistics_data['counters'].get('uploader.files_uploaded', 0)
    print(u"Statistics (for last {}):".format(abbreviate_time(statistics_data['stats']['node.uptime'])), file=options.stdout)
    print(u"    uploaded {} in {} files".format(abbreviate_space(uploaded_bytes), uploaded_files), file=options.stdout)
    print(u"  downloaded {} in {} files".format(abbreviate_space(downloaded_bytes), downloaded_files), file=options.stdout)
    print(u"", file=options.stdout)

    if status_data.get('active', None):
        print(u"Active operations:", file=options.stdout)
        print(
            u"\u2553 {:<5} \u2565 {:<26} \u2565 {:<22} \u2565 {}".format(
                "type",
                "storage index",
                "progress",
                "status message",
            ), file=options.stdout
        )
        print(u"\u255f\u2500{}\u2500\u256b\u2500{}\u2500\u256b\u2500{}\u2500\u256b\u2500{}".format(u'\u2500' * 5, u'\u2500' * 26, u'\u2500' * 22, u'\u2500' * 20), file=options.stdout)
        for op in status_data['active']:
            op_type = 'UKN '
            if 'progress-hash' in op:
                op_type = ' put '
                total = (op['progress-hash'] + op['progress-ciphertext'] + op['progress-encode-push']) / 3.0
                progress_bar = u"{}".format(pretty_progress(total * 100.0, size=15))
            else:
                op_type = ' get '
                total = op['progress']
                progress_bar = u"{}".format(pretty_progress(op['progress'] * 100.0, size=15))
            print(
                u"\u2551 {op_type} \u2551 {storage-index-string} \u2551 {progress_bar} ({total:3}%) \u2551 {status}".format(
                    op_type=op_type,
                    progress_bar=progress_bar,
                    total=int(total * 100.0),
                    **op
                ), file=options.stdout
            )

        print(u"\u2559\u2500{}\u2500\u2568\u2500{}\u2500\u2568\u2500{}\u2500\u2568\u2500{}".format(u'\u2500' * 5, u'\u2500' * 26, u'\u2500' * 22, u'\u2500' * 20), file=options.stdout)
    else:
        print(u"No active operations.", file=options.stdout)

    if status_data.get('recent', None):
        non_verbose_ops = ('upload', 'download')
        recent = [op for op in status_data['recent'] if op['type'] in non_verbose_ops]
        print(u"\nRecent operations:", file=options.stdout)
        if len(recent) or options['verbose']:
            print(
                u"\u2553 {:<5} \u2565 {:<26} \u2565 {:<10} \u2565 {}".format(
                    "type",
                    "storage index",
                    "size",
                    "status message",
                ), file=options.stdout
            )

        op_map = {
            'upload': ' put ',
            'download': ' get ',
            'retrieve': 'retr ',
            'publish': ' pub ',
            'mapupdate': 'mapup',
        }

        ops_to_show = status_data['recent'] if options['verbose'] else recent
        for op in ops_to_show:
            op_type = op_map[op.get('type', None)]
            if op['type'] == 'mapupdate':
                nice_size = op['mode']
            else:
                nice_size = abbreviate_space(op['total-size'])
            print(
                u"\u2551 {op_type} \u2551 {storage-index-string} \u2551 {nice_size:<10} \u2551 {status}".format(
                    op_type=op_type,
                    nice_size=nice_size,
                    **op
                ), file=options.stdout
            )

        if len(recent) or options['verbose']:
            print(u"\u2559\u2500{}\u2500\u2568\u2500{}\u2500\u2568\u2500{}\u2500\u2568\u2500{}".format(u'\u2500' * 5, u'\u2500' * 26, u'\u2500' * 10, u'\u2500' * 20), file=options.stdout)
        skipped = len(status_data['recent']) - len(ops_to_show)
        if not options['verbose'] and skipped:
            print(u"   Skipped {} non-upload/download operations; use --verbose to see".format(skipped), file=options.stdout)
    else:
        print(u"No recent operations.", file=options.stdout)

    # open question: should we return non-zero if there were no
    # operations at all to display?
    return 0


class TahoeStatusCommand(BaseOptions):

    optFlags = [
        ["verbose", "v", "Include publish, retrieve, mapupdate in ops"],
    ]

    def postOptions(self):
        if self.parent['node-directory']:
            self['node-directory'] = argv_to_abspath(self.parent['node-directory'])
        else:
            self['node-directory'] = get_default_nodedir()

    def getSynopsis(self):
        return "Usage: tahoe [global-options] status [options]"

    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += "Various status information"
        return t


subCommands = [
    ["status", None, TahoeStatusCommand,
     "Status."],
]
