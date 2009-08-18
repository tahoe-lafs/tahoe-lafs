# invoke this with a specific python

import sys, shutil, os.path
from subprocess import Popen, PIPE

PYTHON = sys.executable
ARCH = sys.argv[1]

class SubprocessError(Exception):
    pass

def get_output(*cmd, **kwargs):
    tolerate_stderr = kwargs.get("tolerate_stderr", False)
    print " " + " ".join(cmd)
    p = Popen(cmd, stdout=PIPE)
    (out,err) = p.communicate()
    rc = p.returncode
    if rc != 0:
        print >>sys.stderr, err
        raise SubprocessError("command %s exited with rc=%s", (cmd, rc))
    if err and not tolerate_stderr:
        print >>sys.stderr, "stderr:", err
        raise SubprocessError("command emitted unexpected stderr")
    print " =>", out,
    return out

def run(*cmd, **kwargs):
    print " " + " ".join(cmd)
#    if "stdin" in kwargs:
#        stdin = kwargs.pop("stdin")
#        p = Popen(cmd, stdin=PIPE, **kwargs)
#        p.stdin.write(stdin)
#        p.stdin.close()
#    else:
#        p = Popen(cmd, **kwargs)
    p = Popen(cmd, **kwargs)
    rc = p.wait()
    if rc != 0:
        raise SubprocessError("command %s exited with rc=%s", (cmd, rc))

# the very first time you run setup.py, it will download+build darcsver and
# whatnot, emitting noise to stdout. Run it once (and throw away that junk)
# to avoid treating that noise as the package name.
run(PYTHON, "setup.py", "--name")

NAME = get_output(PYTHON, "setup.py", "--name").strip()
VERSION = get_output(PYTHON, "setup.py", "--version").strip()

TARBALL = "%s-%s.tar.gz" % (NAME, VERSION)
DEBIAN_TARBALL = "%s_%s.orig.tar.gz" % (NAME, VERSION)
BUILDDIR = "build/debian/%s-%s" % (NAME, VERSION)

run(PYTHON, "setup.py", "sdist", "--formats=gztar")
if os.path.exists("build/debian"):
    shutil.rmtree("build/debian")
os.makedirs("build/debian")
shutil.copyfile("dist/%s" % TARBALL, "build/debian/%s" % DEBIAN_TARBALL)
run("tar", "xf", DEBIAN_TARBALL, cwd="build/debian")

# now modify the tree for debian packaging. This is an algorithmic way of
# applying the debian .diff, which factors out some of the similarities
# between various debian/ubuntu releases. Everything we do after this point
# will show up in the generated .diff, and thus form the debian-specific part
# of the source package.
DEBDIR = os.path.join(BUILDDIR, "debian")
os.makedirs(DEBDIR)

# The 'aliases' section in setup.cfg causes problems, so get rid of it. We
# could get rid of the whole file, but 1: find_links is still sort of useful,
# and 2: dpkg-buildpackage prefers to ignore file removal (as opposed to
# file-modification)

#os.unlink(os.path.join(BUILDDIR, "setup.cfg"))
SETUPCFG = os.path.join(BUILDDIR, "setup.cfg")
lines = open(SETUPCFG, "r").readlines()
f = open(SETUPCFG, "w")
for l in lines:
    if l.startswith("[aliases]"):
        break
    f.write(l)
f.close()

for n in ["compat", "control", "copyright", "pycompat", "rules"]:
    fn = "misc/debian/%s.%s" % (n, ARCH)
    if not os.path.exists(fn):
        fn = "misc/debian/%s" % n
    assert os.path.exists(fn)
    
    shutil.copyfile(fn, os.path.join(DEBDIR, n))
    if n == "rules":
        os.chmod(os.path.join(DEBDIR, n), 0755) # +x

# We put "local package" on the first line of the changelog entry to suppress
# the lintian NMU warnings (since debchange's new entry's "author" will
# probably be different than the what the debian/control Maintainer: field
# says)

DISTRIBUTION_MAP = {"sid": "unstable"}

run("debchange", "--create",
    "--package", NAME,
    "--newversion", VERSION+"-1",
    "--distribution", DISTRIBUTION_MAP.get(ARCH, ARCH),
    "local package: 'make deb' build", cwd=BUILDDIR)

# the package is ready to build. 'debuild' will produce the source package
# (.dsc+.diff.gz), then build the .deb and produce a .changes file ready for
# upload to an APT archive. The build log will go into a .build file.

run("debuild", "-uc", "-us", cwd=BUILDDIR)
