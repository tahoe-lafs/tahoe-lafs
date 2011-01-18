import os, re

from subprocess import Popen, PIPE

THISDIR_RE=re.compile("What's new in \"(.*)\"")

def exec_darcs(darcscmd):
    cmd = ['darcs'] + darcscmd
    try:
        p = Popen(cmd, stdout=PIPE, stderr=PIPE, universal_newlines=True)
    except EnvironmentError:
        cmd = ['realdarcs.exe'] + darcscmd
        p = Popen(cmd, stdout=PIPE, stderr=PIPE, universal_newlines=True)
        
    output = p.communicate()[0]
    return (p.returncode, output)

def run_darcs_query_manifest():
    return exec_darcs(['query', 'manifest'])

def run_darcs_whatsnew_dot():
    return exec_darcs(['whatsnew', '.'])

def find_files_for_darcs(dirname):
    try:
        unused, whatsnewoutput = run_darcs_whatsnew_dot()
        queryretcode, queryoutput = run_darcs_query_manifest()
    except EnvironmentError:
        if not os.path.exists('PKG-INFO'):
            from distutils import log
            log.info("Unable to execute darcs -- if you are building a package with 'setup.py sdist', 'setup.py bdist_egg', or other package-building commands, then the resulting package might be missing some files.  If you are not building a package then you can ignore this warning.")
        # Oh well -- just return None.
        return

    if queryretcode != 0:
        if not os.path.exists('PKG-INFO'):
            from distutils import log
            log.warn("Failure to get the list of managed files from darcs -- if you are building a package with 'setup.py sdist', 'setup.py bdist_egg', or other package-building commands, then the resulting package might be missing some files.  If you are not building a package then you can ignore this warning.")
        # Oh well -- just return None.
        return

    # We got output.
    mo = THISDIR_RE.search(whatsnewoutput)
    if mo:
        curdirname = mo.group(1)
        while curdirname.endswith('/'):
            curdirname = curdirname[:-1]
        curdirname += "/"
    else:
        curdirname = ""

    # Prepend this directory.
    rel_to_repo_dirname = curdirname + dirname

    # Normalize rel_to_repo_dirname from local form to the form that setuptools uses to the form that "darcs query manifest" outputs (unix form).
    rel_to_repo_dirname = rel_to_repo_dirname.replace('\\', '/')
    while rel_to_repo_dirname.endswith('/'):
        rel_to_repo_dirname = rel_to_repo_dirname[:-1]

    # Append a '/' to make sure we don't match "foobar" when rel_to_repo_dirname is "foo".
    if rel_to_repo_dirname:
        rel_to_repo_dirname += '/'

    warn = True
    for fn in queryoutput.split('\n'):
        if fn == ".":
            continue
        if fn.startswith('./'):
            fn = fn[2:]
        if fn.startswith(rel_to_repo_dirname):
            fn = fn[len(rel_to_repo_dirname):]
            warn = False
            # We need to replace "/" by "\\" because setuptools can't includes web/*.xhtml files on Windows, due of path separator
            # This correct ticket #1033
            yield fn.replace('/', os.sep)

    if warn and not os.path.exists('PKG-INFO'):
        from distutils import log
        log.warn("Didn't find any files in directory \"%s\" (full path: \"%s\") that were managed by darcs revision control -- if you are building a package with 'setup.py sdist', 'setup.py bdist_egg', or other package-building commands, then the resulting package might be missing some files.  If you are not building a package then you can ignore this warning." % (dirname, os.path.abspath(rel_to_repo_dirname),))
