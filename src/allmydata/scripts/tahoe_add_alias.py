
import os.path
from allmydata import uri
from allmydata.scripts.common import get_aliases

def add_alias(options):
    nodedir = options['node-directory']
    alias = options.alias
    cap = options.cap
    stdout = options.stdout
    stderr = options.stderr
    aliasfile = os.path.join(nodedir, "private", "aliases")
    cap = uri.from_string_dirnode(cap).to_string()
    assert ":" not in alias
    assert " " not in alias
    # probably check for others..
    f = open(aliasfile, "a")
    f.write("%s: %s\n" % (alias, cap))
    f.close()
    print >>stdout, "Alias '%s' added" % (alias,)
    return 0

def list_aliases(options):
    nodedir = options['node-directory']
    stdout = options.stdout
    stderr = options.stderr
    aliases = get_aliases(nodedir)
    alias_names = sorted(aliases.keys())
    max_width = max([len(name) for name in alias_names] + [0])
    fmt = "%" + str(max_width) + "s: %s"
    for name in alias_names:
        print >>stdout, fmt % (name, aliases[name])

