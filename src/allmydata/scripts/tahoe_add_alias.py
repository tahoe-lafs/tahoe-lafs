from __future__ import print_function
from __future__ import unicode_literals

import os.path
import codecs
import json

from allmydata.util.assertutil import precondition

from allmydata import uri
from allmydata.scripts.common_http import do_http, check_http_error
from allmydata.scripts.common import get_aliases
from allmydata.util.fileutil import move_into_place
from allmydata.util.encodingutil import quote_output, quote_output_u


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
        show_output(stderr, "Alias {alias} already exists!", alias=alias)
        return 1
    aliasfile = os.path.join(nodedir, "private", "aliases")
    cap = uri.from_string_dirnode(cap).to_string()

    add_line_to_aliasfile(aliasfile, alias, cap)
    show_output(stdout, "Alias {alias} added", alias=alias)
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
        show_output(stderr, "Alias {alias} already exists!", alias=alias)
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
    show_output(stdout, "Alias {alias} created", alias=alias)
    return 0


def show_output(fp, template, **kwargs):
    """
    Print to just about anything.

    :param fp: A file-like object to which to print.  This handles the case
        where ``fp`` declares a support encoding with the ``encoding``
        attribute (eg sys.stdout on Python 3).  It handles the case where
        ``fp`` declares no supported encoding via ``None`` for its
        ``encoding`` attribute (eg sys.stdout on Python 2 when stdout is not a
        tty).  It handles the case where ``fp`` declares an encoding that does
        not support all of the characters in the output by forcing the
        "namereplace" error handler.  It handles the case where there is no
        ``encoding`` attribute at all (eg StringIO.StringIO) by writing
        utf-8-encoded bytes.
    """
    assert isinstance(template, unicode)

    # On Python 3 fp has an encoding attribute under all real usage.  On
    # Python 2, the encoding attribute is None if stdio is not a tty.  The
    # test suite often passes StringIO which has no such attribute.  Make
    # allowances for this until the test suite is fixed and Python 2 is no
    # more.
    try:
        encoding = fp.encoding or "utf-8"
    except AttributeError:
        has_encoding = False
        encoding = "utf-8"
    else:
        has_encoding = True

    output = template.format(**{
        k: quote_output_u(v, encoding=encoding)
        for (k, v)
        in kwargs.items()
    })
    safe_output = output.encode(encoding, "namereplace")
    if has_encoding:
        safe_output = safe_output.decode(encoding)
    print(safe_output, file=fp)


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


def _escape_format(t):
    """
    _escape_format(t).format() == t

    :param unicode t: The text to escape.
    """
    return t.replace("{", "{{").replace("}", "}}")


def list_aliases(options):
    """
    Show aliases that exist.
    """
    data = _get_alias_details(options['node-directory'])

    if options['json']:
        output = _escape_format(json.dumps(data, indent=4).decode("utf-8"))
    else:
        def dircap(details):
            return (
                details['readonly']
                if options['readonly-uri']
                else details['readwrite']
            ).decode("utf-8")

        max_width = max([len(quote_output(name)) for name in data.keys()] + [0])
        fmt = "%" + str(max_width) + "s: %s"
        output = "\n".join(list(
            fmt % (name, dircap(details))
            for name, details
            in data.items()
        ))

    if output:
        # Show whatever we computed.  Skip this if there is no output to avoid
        # a spurious blank line.
        show_output(options.stdout, output)

    return 0
