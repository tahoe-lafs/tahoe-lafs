from __future__ import print_function

import os.path
import codecs
import json

from allmydata.util.assertutil import precondition

from allmydata import uri
from allmydata.scripts.common_http import do_http, check_http_error
from allmydata.scripts.common import get_aliases
from allmydata.util.fileutil import move_into_place
from allmydata.util.encodingutil import unicode_to_output, quote_output


def add_line_to_aliasfile(aliasfile, alias, cap):
    # we use os.path.exists, rather than catching EnvironmentError, to avoid
    # clobbering the valuable alias file in case of spurious or transient
    # filesystem errors.
    if os.path.exists(aliasfile):
        f = codecs.open(aliasfile, "r", "utf-8")
        aliases = f.read()
        f.close()
        if not aliases.endswith("\n"):
            aliases += "\n"
    else:
        aliases = ""
    aliases += "%s: %s\n" % (alias, cap)
    f = codecs.open(aliasfile+".tmp", "w", "utf-8")
    f.write(aliases)
    f.close()
    move_into_place(aliasfile+".tmp", aliasfile)

def add_alias(options):
    nodedir = options['node-directory']
    alias = options.alias
    precondition(isinstance(alias, unicode), alias=alias)
    cap = options.cap
    stdout = options.stdout
    stderr = options.stderr
    if u":" in alias:
        # a single trailing colon will already have been stripped if present
        print("Alias names cannot contain colons.", file=stderr)
        return 1
    if u" " in alias:
        print("Alias names cannot contain spaces.", file=stderr)
        return 1

    old_aliases = get_aliases(nodedir)
    if alias in old_aliases:
        print("Alias %s already exists!" % quote_output(alias), file=stderr)
        return 1
    aliasfile = os.path.join(nodedir, "private", "aliases")
    cap = uri.from_string_dirnode(cap).to_string()

    add_line_to_aliasfile(aliasfile, alias, cap)

    print("Alias %s added" % quote_output(alias), file=stdout)
    return 0

def create_alias(options):
    # mkdir+add_alias
    nodedir = options['node-directory']
    alias = options.alias
    precondition(isinstance(alias, unicode), alias=alias)
    stdout = options.stdout
    stderr = options.stderr
    if u":" in alias:
        # a single trailing colon will already have been stripped if present
        print("Alias names cannot contain colons.", file=stderr)
        return 1
    if u" " in alias:
        print("Alias names cannot contain spaces.", file=stderr)
        return 1

    old_aliases = get_aliases(nodedir)
    if alias in old_aliases:
        print("Alias %s already exists!" % quote_output(alias), file=stderr)
        return 1

    aliasfile = os.path.join(nodedir, "private", "aliases")

    nodeurl = options['node-url']
    if not nodeurl.endswith("/"):
        nodeurl += "/"
    url = nodeurl + "uri?t=mkdir"
    resp = do_http("POST", url)
    rc = check_http_error(resp, stderr)
    if rc:
        return rc
    new_uri = resp.read().strip()

    # probably check for others..

    add_line_to_aliasfile(aliasfile, alias, new_uri)

    print("Alias %s created" % (quote_output(alias),), file=stdout)
    return 0


def _get_alias_details(nodedir):
    aliases = get_aliases(nodedir)
    alias_names = sorted(aliases.keys())
    data = {}
    for name in alias_names:
        dircap = uri.from_string(aliases[name])
        data[name] = {
            "readwrite": dircap.to_string(),
            "readonly": dircap.get_readonly().to_string(),
        }
    return data


def list_aliases(options):
    nodedir = options['node-directory']
    stdout = options.stdout
    stderr = options.stderr

    data = _get_alias_details(nodedir)

    max_width = max([len(quote_output(name)) for name in data.keys()] + [0])
    fmt = "%" + str(max_width) + "s: %s"
    rc = 0

    if options['json']:
        try:
            # XXX why are we presuming utf-8 output?
            print(json.dumps(data, indent=4).decode('utf-8'), file=stdout)
        except (UnicodeEncodeError, UnicodeDecodeError):
            print(json.dumps(data, indent=4), file=stderr)
            rc = 1
    else:
        for name, details in data.items():
            dircap = details['readonly'] if options['readonly-uri'] else details['readwrite']
            try:
                print(fmt % (unicode_to_output(name), unicode_to_output(dircap.decode('utf-8'))), file=stdout)
            except (UnicodeEncodeError, UnicodeDecodeError):
                print(fmt % (quote_output(name), quote_output(dircap)), file=stderr)
                rc = 1

    if rc == 1:
        print("\nThis listing included aliases or caps that could not be converted to the terminal" \
                        "\noutput encoding. These are shown using backslash escapes and in quotes.", file=stderr)
    return rc
