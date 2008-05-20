
import os.path
from allmydata import uri

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

