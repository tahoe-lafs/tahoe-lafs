#! /usr/bin/env python

"""
Create src/allmydata/version.py, based upon the latest darcs release tag.

If your source tree is coming from darcs (i.e. there exists a _darcs
directory), this tool will determine the most recent release tag, count the
patches that have been applied since then, and compute a version number to be
written into version.py . This version number will be available by doing:

 from allmydata import __version__

Source trees that do not come from darcs (release tarballs, nightly tarballs)
do not have a _darcs directory. Instead, they should have a version.py that
was generated before the tarball was produced. In this case, this script will
quietly exit without modifying the existing version.py .

FYI, src/allmydata/__init__.py will attempt to import version.py and use the
version number therein. If it cannot, it will announce a version of
'UNKNOWN'. This should only happen if someone manages to get hold of a
non-_darcs/ source tree.

'release tags' are tags in the tahoe source tree that match the following
regexp:

 ^allmydata-tahoe-\d+\.\d+\.\d+\w*$

This excludes zfec tags (which start with 'zfec '). It also excludes
'developer convenience tags', which look like 'hoping to fix bug -warner'.
(the original goal was to use release tags that lacked the 'allmydata-tahoe-'
prefix, but it turns out to be more efficient to keep it in, because I can't
get 'darcs changes --from-tag=' to accept real regexps).

"""

import os, sys, re
import xml.dom.minidom
from subprocess import Popen, PIPE

def get_text(nodelist):
    rc = ""
    for node in nodelist:
        if node.nodeType == node.TEXT_NODE:
            rc = rc + node.data
    return rc

VERSION_BODY = '''
from util.version import Version

# This is the version of this tree, as created by misc/make-version.py from
# the Darcs patch information: the main version number is taken from the most
# recent release tag. If some patches have been added since the last release,
# this will have a -NN "build number" suffix. Please see
# allmydata.util.version for a description of what the different fields mean.

verstr = "%s"
__version__ = Version(verstr)
'''

def write_version_py(verstr):
    f = open("src/allmydata/version.py", "wt")
    f.write(VERSION_BODY % (verstr,))
    f.close()

def update():
    if not os.path.exists("_darcs") or not os.path.isdir("_darcs"):
        if os.path.exists("src/allmydata/version.py"):
            print "no _darcs/ and version.py exists, leaving it alone"
            return 0
        print "no _darcs/ but no version.py either: how did you get this tree?"
        return 0
    darcs = 'darcs'
    if sys.platform == 'win32':
        darcs = 'realdarcs'
    cmd = [darcs, "changes", "--from-tag=^allmydata-tahoe", "--xml-output"]
    try:
        p = Popen(cmd, stdout=PIPE)
        output = p.communicate()[0]
        rc = p.returncode
    except EnvironmentError, le:
        output = "There was an environment error: %s" % (le,)
        rc = -1

    if rc != 0:
        print "unable to run 'darcs changes':"
        print output
        print "so I'm leaving version.py alone"
        return 0

    try:
        doc = xml.dom.minidom.parseString(output)
    except xml.parsers.expat.ExpatError:
        print "unable to parse darcs XML output:"
        print output
        raise
    changelog = doc.getElementsByTagName("changelog")[0]
    patches = changelog.getElementsByTagName("patch")
    count = 0
    version_re = re.compile("^TAG allmydata-tahoe-(\d+\.\d+\.\d+\w*)$")
    for patch in patches:
        name = get_text(patch.getElementsByTagName("name")[0].childNodes)
        m = version_re.match(name)
        if m:
            last_tag = m.group(1)
            last_tag = last_tag.encode("ascii")
            break
        count += 1
    else:
        print "unable to find a matching tag"
        print output
        print "so I'm leaving version.py alone"
        return 0

    if count:
        # this is an interim version
        verstr = "%s-%d" % (last_tag, count)
    else:
        # this is a release
        verstr = last_tag

    write_version_py(verstr)
    print "wrote '%s' into src/allmydata/version.py" % (verstr,)
    return 0

if __name__ == '__main__':
    rc = update()
    sys.exit(rc)

