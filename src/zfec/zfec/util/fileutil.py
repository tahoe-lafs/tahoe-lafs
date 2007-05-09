"""
Futz with files like a pro.
"""

import exceptions, os, stat, tempfile, time

try:
    from twisted.python import log
except ImportError:
    class DummyLog:
        def msg(self, *args, **kwargs):
            pass
    log = DummyLog()

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

class _Dir(object):
    """
    Hold a set of files and subdirs and clean them all up when asked to.
    """
    def __init__(self, name, cleanup=True):
        self.name = name
        self.cleanup = cleanup
        self.files = set()
        self.subdirs = set()

    def file(self, fname, mode=None):
        """
        Create a file in the tempdir and remember it so as to close() it
        before attempting to cleanup the temp dir.

        @rtype: file
        """
        ffn = os.path.join(self.name, fname)
        if mode is not None:
            fo = open(ffn, mode)
        else:
            fo = open(ffn)
        self.register_file(fo)
        return fo
       
    def subdir(self, dirname):
        """
        Create a subdirectory in the tempdir and remember it so as to call
        shutdown() on it before attempting to clean up.

        @rtype: NamedTemporaryDirectory instance
        """
        ffn = os.path.join(self.name, dirname)
        sd = _Dir(ffn, self.cleanup)
        self.register_subdir(sd)
       
    def register_file(self, fileobj):
        """
        Remember the file object and call close() on it before attempting to
        clean up.
        """
        self.files.add(fileobj)
       
    def register_subdir(self, dirobj):
        """
        Remember the _Dir object and call shutdown() on it before attempting
        to clean up.
        """
        self.subdirs.add(dirobj)
       
    def shutdown(self):
        if self.cleanup:
            for subdir in hasattr(self, 'subdirs') and self.subdirs or []:
                subdir.shutdown()
            for fileobj in hasattr(self, 'files') and self.files or []:
                fileobj.close() # "close()" is idempotent so we don't need to catch exceptions here
            if hasattr(self, 'name'):
                rm_dir(self.name)

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

class NamedTemporaryDirectory(_Dir):
    """
    Call tempfile.mkdtemp(), store the name of the dir in self.name, and
    rm_dir() when it gets garbage collected or "shutdown()".

    Also optionally keep track of file objects for files within the tempdir
    and call close() on them before rm_dir().  This is a convenient way to
    open temp files within the directory, and it is very helpful on Windows
    because you can't delete a directory which contains a file which is
    currently open.
    """
    def __init__(self, cleanup=True, *args, **kwargs):
        """ If cleanup, then the directory will be rmrf'ed when the object is shutdown. """
        name = tempfile.mkdtemp(*args, **kwargs)
        _Dir.__init__(self, name, cleanup)

def make_dirs(dirname, mode=0777, strictmode=False):
    """
    A threadsafe and idempotent version of os.makedirs().  If the dir already
    exists, do nothing and return without raising an exception.  If this call
    creates the dir, return without raising an exception.  If there is an
    error that prevents creation or if the directory gets deleted after
    make_dirs() creates it and before make_dirs() checks that it exists, raise
    an exception.

    @param strictmode if true, then make_dirs() will raise an exception if the
        directory doesn't have the desired mode.  For example, if the
        directory already exists, and has a different mode than the one
        specified by the mode parameter, then if strictmode is true,
        make_dirs() will raise an exception, else it will ignore the
        discrepancy.
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

    tx = None
    if hasattr(os, 'chmod'):
        try:
            os.chmod(dirname, mode)
        except OSError, x:
            tx = x

    if strictmode and hasattr(os, 'stat'):
        s = os.stat(dirname)
        resmode = stat.S_IMODE(s.st_mode)
        if resmode != mode:
            if tx:
                raise tx
            raise exceptions.IOError, "unknown error prevented setting correct mode of directory, or changed mode of the directory immediately after creation.  dirname: %s, mode: %04o, resmode: %04o" % (dirname, mode, resmode,)  # careful not to construct an IOError with a 2-tuple, as that has a special meaning...

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

# zfec -- fast forward error correction library with Python interface
# 
# Copyright (C) 2007 Allmydata, Inc.
# Author: Zooko Wilcox-O'Hearn
# 
# This file is part of zfec.
# 
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version, with the added permission that, if you become obligated
# to release a derived work under this licence (as per section 2.b), you may
# delay the fulfillment of this obligation for up to 12 months.  See the file
# COPYING for details.
#
# If you would like to inquire about a commercial relationship with Allmydata,
# Inc., please contact partnerships@allmydata.com and visit
# http://allmydata.com/.
