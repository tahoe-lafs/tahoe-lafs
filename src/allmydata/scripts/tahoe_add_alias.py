
import os.path
from allmydata import uri
from allmydata.scripts.common import get_aliases

def add_alias(nodedir, alias, cap, stdout, stderr):
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

def list_aliases(nodedir, stdout, stderr):
    aliases = get_aliases(nodedir)
    alias_names = sorted(aliases.keys())
    max_width = max([len(name) for name in alias_names] + [0])
    fmt = "%" + str(max_width) + "s: %s"
    for name in alias_names:
        print >>stdout, fmt % (name, aliases[name])

