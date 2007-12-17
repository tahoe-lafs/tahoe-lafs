#  Copyright (c) 2000 Autonomous Zone Industries
#  Copyright (c) 2002-2007 Bryce "Zooko" Wilcox-O'Hearn
#  This file is licensed under the
#    GNU Lesser General Public License v2.1.
#    See the file COPYING or visit http://www.gnu.org/ for details.
# Portions snarfed out of the Python standard library.
# The du part is due to Jim McCoy.

"""
Futz with files like a pro.
"""

import exceptions, os, stat, tempfile, time

from twisted.python import log

def rename(src, dst, tries=4, basedelay=0.1):
    """ Here is a superkludge to workaround the fact that occasionally on
    Windows some other process (e.g. an anti-virus scanner, a local search
    engine, etc.) is looking at your file when you want to delete or move it,
    and hence you can't.  The horrible workaround is to sit and spin, trying
    to delete it, for a short time and then give up.

    With the default values of tries and basedelay this can block for less
    than a second.

    @param tries: number of tries -- each time after the first we wait twice
    as long as the previous wait
    @param basedelay: how long to wait before the second try
    """
    for i in range(tries-1):
        try:
            return os.rename(src, dst)
        except EnvironmentError, le:
            # XXX Tighten this to check if this is a permission denied error (possibly due to another Windows process having the file open and execute the superkludge only in this case.
            log.msg("XXX KLUDGE Attempting to move file %s => %s; got %s; sleeping %s seconds" % (src, dst, le, basedelay,))
            time.sleep(basedelay)
            basedelay *= 2
    return os.rename(src, dst) # The last try.

def remove(f, tries=4, basedelay=0.1):
    """ Here is a superkludge to workaround the fact that occasionally on
    Windows some other process (e.g. an anti-virus scanner, a local search
    engine, etc.) is looking at your file when you want to delete or move it,
    and hence you can't.  The horrible workaround is to sit and spin, trying
    to delete it, for a short time and then give up.

    With the default values of tries and basedelay this can block for less
    than a second.

    @param tries: number of tries -- each time after the first we wait twice
    as long as the previous wait
    @param basedelay: how long to wait before the second try
    """
    try:
        os.chmod(f, stat.S_IWRITE | stat.S_IEXEC | stat.S_IREAD)
    except:
        pass
    for i in range(tries-1):
        try:
            return os.remove(f)
        except EnvironmentError, le:
            # XXX Tighten this to check if this is a permission denied error (possibly due to another Windows process having the file open and execute the superkludge only in this case.
            if not os.path.exists(f):
                return
            log.msg("XXX KLUDGE Attempting to remove file %s; got %s; sleeping %s seconds" % (f, le, basedelay,))
            time.sleep(basedelay)
            basedelay *= 2
    return os.remove(f) # The last try.

class NamedTemporaryDirectory:
    """
    This calls tempfile.mkdtemp(), stores the name of the dir in
    self.name, and rmrf's the dir when it gets garbage collected or
    "shutdown()".
    """
    def __init__(self, cleanup=True, *args, **kwargs):
        """ If cleanup, then the directory will be rmrf'ed when the object is shutdown. """
        self.cleanup = cleanup
        self.name = tempfile.mkdtemp(*args, **kwargs)

    def __repr__(self):
        return "<%s instance at %x %s>" % (self.__class__.__name__, id(self), self.name)

    def __str__(self):
        return self.__repr__()

    def __del__(self):
        try:
            self.shutdown()
        except:
            import traceback
            traceback.print_exc()

    def shutdown(self):
        if self.cleanup and hasattr(self, 'name'):
            rm_dir(self.name)

def make_dirs(dirname, mode=0777):
    """
    An idempotent version of os.makedirs().  If the dir already exists, do
    nothing and return without raising an exception.  If this call creates the
    dir, return without raising an exception.  If there is an error that
    prevents creation or if the directory gets deleted after make_dirs() creates
    it and before make_dirs() checks that it exists, raise an exception.
    """
    tx = None
    try:
        os.makedirs(dirname, mode)
    except OSError, x:
        tx = x

    if not os.path.isdir(dirname):
        if tx:
            raise tx
        raise exceptions.IOError, "unknown error prevented creation of directory, or deleted the directory immediately after creation: %s" % dirname # careful not to construct an IOError with a 2-tuple, as that has a special meaning...

def rm_dir(dirname):
    """
    A threadsafe and idempotent version of shutil.rmtree().  If the dir is
    already gone, do nothing and return without raising an exception.  If this
    call removes the dir, return without raising an exception.  If there is an
    error that prevents deletion or if the directory gets created again after
    rm_dir() deletes it and before rm_dir() checks that it is gone, raise an
    exception.
    """
    excs = []
    try:
        os.chmod(dirname, stat.S_IWRITE | stat.S_IEXEC | stat.S_IREAD)
        for f in os.listdir(dirname):
            fullname = os.path.join(dirname, f)
            if os.path.isdir(fullname):
                rm_dir(fullname)
            else:
                remove(fullname)
        os.rmdir(dirname)
    except Exception, le:
        # Ignore "No such file or directory"
        if (not isinstance(le, OSError)) or le.args[0] != 2:
            excs.append(le)

    # Okay, now we've recursively removed everything, ignoring any "No
    # such file or directory" errors, and collecting any other errors.

    if os.path.exists(dirname):
        if len(excs) == 1:
            raise excs[0]
        if len(excs) == 0:
            raise OSError, "Failed to remove dir for unknown reason."
        raise OSError, excs


def remove_if_possible(f):
    try:
        remove(f)
    except:
        pass

def open_or_create(fname, binarymode=True):
    try:
        return open(fname, binarymode and "r+b" or "r+")
    except EnvironmentError:
        return open(fname, binarymode and "w+b" or "w+")


def du(basedir):
    size = 0

    for root, dirs, files in os.walk(basedir):
        for f in files:
            fn = os.path.join(root, f)
            size += os.path.getsize(fn)

    return size
