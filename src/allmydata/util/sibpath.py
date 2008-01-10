import os
import sys
from twisted.python.util import sibpath as tsibpath

def sibpath(path, sibling):
    """
    Looks for a named sibling relative to the given path.  If such a file
    exists, its path will be returned, otherwise a second search will be
    made for the named sibling relative to the path of the executable
    currently running.  This is useful in the case that something built
    with py2exe, for example, needs to find data files relative to its
    install.  Note hence that care should be taken not to search for
    private package files whose names might collide with files which might
    be found installed alongside the python interpreter itself.  If no
    file is found in either place, the sibling relative to the given path
    is returned, likely leading to a file not found error.
    """
    sib = tsibpath(path, sibling)
    if not os.path.exists(sib):
        exe_sib = tsibpath(sys.executable, sibling)
        if os.path.exists(exe_sib):
            return exe_sib
    return sib

