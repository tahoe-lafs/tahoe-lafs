import os, string, sys, re
import xml.dom.minidom
import xml.parsers.expat
from subprocess import Popen, PIPE
from distutils import log

try:
    # If we can import pyutil.version_class then use its regex.
    from pyutil import version_class
    VERSION_BASE_RE_STR = version_class.VERSION_BASE_RE_STR
except ImportError:
    # Else (perhaps a bootstrapping problem),then we'll use this
    # regex, which was copied from the pyutil source code on
    # 2007-10-30.
    VERSION_BASE_RE_STR="(\d+)(\.(\d+)(\.(\d+))?)?((a|b|c)(\d+))?"

def get_text(nodelist):
    rc = ""
    for node in nodelist:
        if node.nodeType == node.TEXT_NODE:
            rc = rc + node.data
    return rc

VERSION_BODY = '''
# This is the version of this tree, as created by %s from the Darcs patch
# information: the main version number is taken from the most recent release
# tag. If some patches have been added since the last release, this will have a
# -NN "build number" suffix, or else a -rNN "revision number" suffix. Please see
# pyutil.version_class for a description of what the different fields mean.

verstr = "%s"
try:
    from pyutil.version_class import Version as pyutil_Version
    __version__ = pyutil_Version(verstr)
except (ImportError, ValueError):
    # Maybe there is no pyutil installed, or this may be an older version of
    # pyutil.version_class which does not support SVN-alike revision numbers.
    from distutils.version import LooseVersion as distutils_Version
    __version__ = distutils_Version(verstr)
'''

def write_version_py(verstr, outfname, EXE_NAME):
    f = open(outfname, "wt+")
    f.write(VERSION_BODY % (EXE_NAME, verstr,))
    f.close()

def read_version_py(infname):
    try:
        verstrline = open(infname, "rt").read()
    except EnvironmentError:
        return None
    else:
        VSRE = r"^verstr = ['\"]([^'\"]*)['\"]"
        mo = re.search(VSRE, verstrline, re.M)
        if mo:
            return mo.group(1)

def update(pkgname, verfilename, revision_number=False, loud=False, abort_if_snapshot=False, EXE_NAME="darcsver"):
    """
    @param revision_number If true, count the total number of patches in all
    history.  If false, count the total number of patches since the most recent
    release tag.

    Returns a tuple of (exit code, new version string).
    """
    rc = -1
    cmd = ["changes", "--xml-output"]
    if not revision_number:
        cmd.append("--from-tag=^%s" % (pkgname,))

    errput = None
    try:
        p = Popen(["darcs"] + cmd, stdout=PIPE, stderr=PIPE, universal_newlines=True)
    except OSError, ose:
        if ose.errno == 2 and '~' in os.environ['PATH']:
            expanded_path = os.environ['PATH'].replace('~', os.path.expanduser('~'))
            msg = ("WARNING: 'darcs' was not found. However '~' was found in your PATH. \n"
                   "Please note that bugs in python cause it to fail to traverse '~' in \n"
                   "the user's PATH.  Please fix your path, e.g. \nPATH=%s" )
            log.warn(msg % (expanded_path,))
        pass
    else:
        (output, errput) = p.communicate()
        rc = p.returncode
    if rc != 0:
        try:
            p = Popen(["realdarcs.exe"] + cmd, stdout=PIPE, stderr=PIPE, universal_newlines=True)
        except OSError, ose:
            if ose.errno == 2 and '~' in os.environ['PATH']:
                expanded_path = os.environ['PATH'].replace('~', os.path.expanduser('~'))
                msg = ("WARNING: 'realdarcs.exe' was not found. However '~' was found in your PATH. \n"
                       "Please note that bugs in python cause it to fail to traverse '~' in \n"
                       "the user's PATH.  Please fix your path, e.g. \nPATH=%s" )
                log.warn(msg % (expanded_path,))
            pass
        else:
            (output, errput) = p.communicate()
            rc = p.returncode
        if rc != 0:
            if errput:
                log.info("%s: darcs wrote to stderr: '%s'" % (EXE_NAME, errput,))
            if os.path.exists(verfilename):
                log.info("%s: Failure from attempt to find version tags with 'darcs changes', and %s already exists, so leaving it alone." % (EXE_NAME, verfilename,))
                return (0, read_version_py(verfilename))
            else:
                log.warn("%s: Failure from attempt to find version tags with 'darcs changes', and %s doesn't exist." % (EXE_NAME, verfilename))
            return (rc, None)

    # Filter out bad chars that can cause the XML parser to give up in despair.
    # (Thanks to lelit of the tailor project and ndurner and warner for this hack.)
    allbadchars = "".join([chr(i) for i in range(0x0a) + [0x0b, 0x0c] + range(0x0e, 0x20) + range(0x7f,0x100)])
    tt = string.maketrans(allbadchars, "-"*len(allbadchars))
    output = output.translate(tt)

    # strip off trailing warning messages that darcs 2.3.1 writes to stdout
    endi = output.find("</changelog>")+len("</changelog>")
    output = output[:endi]
    try:
        doc = xml.dom.minidom.parseString(output)
    except xml.parsers.expat.ExpatError, le:
        le.args = tuple(le.args + (output,))
        raise

    changelog = doc.getElementsByTagName("changelog")[0]
    patches = changelog.getElementsByTagName("patch")
    regexstr = "^TAG %s-(%s)" % (pkgname, VERSION_BASE_RE_STR,)
    version_re = re.compile(regexstr)
    last_tag = None
    count_since_last_patch = 0
    if abort_if_snapshot:
        for patch in patches:
            name = get_text(patch.getElementsByTagName("name")[0].childNodes)
            m = version_re.match(name)
            if m:
                last_tag = m.group(1)
                last_tag = last_tag.encode("utf-8")
                break
            else:
                sys.exit(0) # because abort_if_snapshot
    else:
        for patch in patches:
            name = get_text(patch.getElementsByTagName("name")[0].childNodes)
            m = version_re.match(name)
            if m:
                last_tag = m.group(1)
                last_tag = last_tag.encode("utf-8")
                break
            else:
                count_since_last_patch += 1

    if not last_tag:
        if errput:
            log.info("%s: darcs wrote to stderr: '%s'" % (EXE_NAME, errput,))
        if os.path.exists(verfilename):
            log.warn("%s: I'm unable to find a tag in the darcs history matching \"%s\", so I'm leaving %s alone." % (EXE_NAME, regexstr, verfilename,))
            return (0, read_version_py(verfilename))
        else:
            log.warn("%s: I'm unable to find a tag in the darcs history matching \"%s\", and %s doesn't exist." % (EXE_NAME, regexstr, verfilename,))
            return (0, None)

    if revision_number:
        if count_since_last_patch:
            # this is an interim version
            verstr = "%s-r%d" % (last_tag, len(patches))
        else:
            # this is a release
            verstr = last_tag
    else:
        if count_since_last_patch:
            # this is an interim version
            verstr = "%s-%d" % (last_tag, count_since_last_patch)
        else:
            # this is a release
            verstr = last_tag

    write_version_py(verstr, verfilename, EXE_NAME)
    log.info("%s: wrote '%s' into %s" % (EXE_NAME, verstr, verfilename,))
    return (0, verstr)
