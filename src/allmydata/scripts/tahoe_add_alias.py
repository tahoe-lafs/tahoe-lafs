
import os.path
import codecs
import sys
from allmydata import uri
from allmydata.scripts.common_http import do_http, check_http_error
from allmydata.scripts.common import get_aliases
from allmydata.util.fileutil import move_into_place
from allmydata.util.stringutils import unicode_to_stdout


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
    cap = options.cap
    stdout = options.stdout
    stderr = options.stderr
    assert ":" not in alias
    assert " " not in alias

    old_aliases = get_aliases(nodedir)
    if alias in old_aliases:
        print >>stderr, "Alias '%s' already exists!" % alias
        return 1
    aliasfile = os.path.join(nodedir, "private", "aliases")
    cap = uri.from_string_dirnode(cap).to_string()

    add_line_to_aliasfile(aliasfile, alias, cap)

    print >>stdout, "Alias '%s' added" % (unicode_to_stdout(alias),)
    return 0

def create_alias(options):
    # mkdir+add_alias
    nodedir = options['node-directory']
    alias = options.alias
    stdout = options.stdout
    stderr = options.stderr
    assert ":" not in alias
    assert " " not in alias

    old_aliases = get_aliases(nodedir)
    if alias in old_aliases:
        print >>stderr, "Alias '%s' already exists!" % alias
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

    print >>stdout, "Alias '%s' created" % (unicode_to_stdout(alias),)
    return 0

def list_aliases(options):
    nodedir = options['node-directory']
    stdout = options.stdout
    aliases = get_aliases(nodedir)
    alias_names = sorted(aliases.keys())
    max_width = max([len(name) for name in alias_names] + [0])
    fmt = "%" + str(max_width) + "s: %s"
    for name in alias_names:
        print >>stdout, fmt % (name, aliases[name])

