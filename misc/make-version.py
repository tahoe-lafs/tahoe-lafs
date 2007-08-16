#! /usr/bin/env python

import os, sys

"""
Create _version.py, based upon the latest darcs release tag.

If your source tree is coming from darcs (i.e. it is in a darcs repository),
this tool will determine the most recent release tag, count the patches that
have been applied since then, and compute a version number to be written into
_version.py . This version number will be available by doing:

 from your_package_name import __version__

Source trees that do not come from darcs (e.g. release tarballs, nightly
tarballs) and are not within a darcs repository should instead, come with a
_version.py that was generated before the tarball was produced. In this case,
this script will quietly exit without modifying the existing _version.py .

'release tags' are tags in the source repository that match the following
regexp:

 ^your_package_name-\d+\.\d+(\.\d+)?((a|b|c)(\d+)?)?\w*$

"""

import os, sys, re
import xml.dom.minidom
from subprocess import Popen, PIPE

try:
    # If we can import allmydata.util.version_class then use its regex.
    from allmydata.util import version_class
    VERSION_BASE_RE_STR = version_class.VERSION_BASE_RE_STR
except ImportError:
    # Else (perhaps a bootstrapping problem),then we'll use this
    # regex, which was copied from the pyutil source code on
    # 2007-08-11.
    VERSION_BASE_RE_STR="(\d+)\.(\d+)(\.(\d+))?((a|b|c)(\d+))?"

def get_text(nodelist):
    rc = ""
    for node in nodelist:
        if node.nodeType == node.TEXT_NODE:
            rc = rc + node.data
    return rc

VERSION_BODY = '''
from allmydata.util.version_class import Version

# This is the version of this tree, as created by scripts/make-version.py from
# the Darcs patch information: the main version number is taken from the most
# recent release tag. If some patches have been added since the last release,
# this will have a -NN "build number" suffix. Please see
# allmydata.util.version_class for a description of what the different fields 
# mean.

verstr = "%s"
__version__ = Version(verstr)
'''

def write_version_py(verstr, outfname):
    f = open(outfname, "wt+")
    f.write(VERSION_BODY % (verstr,))
    f.close()

def update(pkgname, verfilename):
    rc = -1
    cmd = ["darcs", "changes", "--from-tag=^%s" % (pkgname,), "--xml-output"]
    try:
        p = Popen(cmd, stdout=PIPE)
    except:
        pass
    else:
        output = p.communicate()[0]
        rc = p.returncode
    if rc != 0:
        cmd = ["realdarcs.exe", "changes", "--from-tag=^%s" % (pkgname,), "--xml-output"]
        p = Popen(cmd, stdout=PIPE)
        output = p.communicate()[0]
        rc = p.returncode
        if rc != 0:
            if os.path.exists(verfilename):
                print "Failure from attempt to find version tags with 'darcs changes', and %s already exists, so leaving it alone." % (verfilename,)
                return 0
            else:
                print "Failure from attempt to find version tags with 'darcs changes', and %s doesn't exist." % (verfilename,)
                return rc

    doc = xml.dom.minidom.parseString(output)
    changelog = doc.getElementsByTagName("changelog")[0]
    patches = changelog.getElementsByTagName("patch")
    count = 0
    regexstr = "^TAG %s-(%s)" % (pkgname, VERSION_BASE_RE_STR,)
    version_re = re.compile(regexstr)
    for patch in patches:
        name = get_text(patch.getElementsByTagName("name")[0].childNodes)
        m = version_re.match(name)
        if m:
            last_tag = m.group(1)
            last_tag = last_tag.encode("ascii")
            break
        count += 1
    else:
        print "I'm unable to find a tag in the darcs history matching \"%s\", so I'm leaving %s alone." % (regexstr, verfilename,)
        return 0

    if count:
        # this is an interim version
        verstr = "%s-%d" % (last_tag, count)
    else:
        # this is a release
        verstr = last_tag

    write_version_py(verstr, verfilename)
    print "wrote '%s' into %s" % (verstr, verfilename,)
    return 0

if __name__ == '__main__':
    if len(sys.argv) >= 2:
        pkgname = sys.argv[1]
    else:
        pkgname = os.path.basename(os.getcwd())
        print "You didn't pass a pkg-name on the command-line, so I'm going to take the name of the current working directory: \"%s\"" % (pkgname,)

    if len(sys.argv) >= 3:
        verfilename = sys.argv[2]
    else:
        verfilename = os.path.join(pkgname, "_version.py")
        print "You didn't pass a verfilename on the command-line, so I'm going to build one from the name of the package: \"%s\"" % (verfilename,)

    rc = update(pkgname=pkgname, verfilename=verfilename)
    sys.exit(rc)

