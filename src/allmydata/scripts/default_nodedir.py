
import sys
from allmydata.util.assertutil import precondition
from allmydata.util.fileutil import abspath_expanduser_unicode

_default_nodedir = None
if sys.platform == 'win32':
    from allmydata.windows import registry
    path = registry.get_base_dir_path()
    if path:
        precondition(isinstance(path, unicode), path)
        _default_nodedir = abspath_expanduser_unicode(path)

if _default_nodedir is None:
    path = abspath_expanduser_unicode(u"~/.tahoe")
    precondition(isinstance(path, unicode), path)
    _default_nodedir = path
