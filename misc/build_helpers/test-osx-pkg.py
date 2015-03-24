# This script treats the OS X pkg as an xar archive and uncompresses it to
# the filesystem. The xar file contains a file called Payload, which is a
# gziped cpio archive of the filesystem. It then cd's into the file system
# and executes '$appname --version-and-path' and checks whether the output
# of that command is right.

# If all of the paths listed therein are loaded from within the current PWD
# then it exits with code 0.

# If anything goes wrong then it exits with non-zero (failure).  This is to
# check that the Mac OS '.pkg' package that gets built is correctly loading
# all of its packages from inside the image.

# Here is an example output from --version-and-path:

# allmydata-tahoe: 1.10.0.post185.dev0 [2249-deps-and-osx-packaging-1: 76ac53846042d9a4095995be92af66cdc09d5ad0-dirty] (/Applications/tahoe.app/src)
# foolscap: 0.7.0 (/Applications/tahoe.app/support/lib/python2.7/site-packages/foolscap-0.7.0-py2.7.egg)
# pycryptopp: 0.6.0.1206569328141510525648634803928199668821045408958 (/Applications/tahoe.app/support/lib/python2.7/site-packages/pycryptopp-0.6.0.1206569328141510525648634803928199668821045408958-py2.7-macosx-10.9-intel.egg)
# zfec: 1.4.24 (/Applications/tahoe.app/support/lib/python2.7/site-packages/zfec-1.4.24-py2.7-macosx-10.9-intel.egg)
# Twisted: 13.0.0 (/Applications/tahoe.app/support/lib/python2.7/site-packages/Twisted-13.0.0-py2.7-macosx-10.9-intel.egg)
# Nevow: 0.11.1 (/Applications/tahoe.app/support/lib/python2.7/site-packages/Nevow-0.11.1-py2.7.egg)
# zope.interface: unknown (/System/Library/Frameworks/Python.framework/Versions/2.7/Extras/lib/python/zope)
# python: 2.7.5 (/usr/bin/python)
# platform: Darwin-13.4.0-x86_64-i386-64bit (None)
# pyOpenSSL: 0.13 (/System/Library/Frameworks/Python.framework/Versions/2.7/Extras/lib/python)
# simplejson: 3.6.4 (/Applications/tahoe.app/support/lib/python2.7/site-packages/simplejson-3.6.4-py2.7-macosx-10.9-intel.egg)
# pycrypto: 2.6.1 (/Applications/tahoe.app/support/lib/python2.7/site-packages/pycrypto-2.6.1-py2.7-macosx-10.9-intel.egg)
# pyasn1: 0.1.7 (/Applications/tahoe.app/support/lib/python2.7/site-packages/pyasn1-0.1.7-py2.7.egg)
# mock: 1.0.1 (/Applications/tahoe.app/support/lib/python2.7/site-packages)
# setuptools: 0.6c16dev5 (/Applications/tahoe.app/support/lib/python2.7/site-packages/setuptools-0.6c16dev5.egg)
# service-identity: 14.0.0 (/Applications/tahoe.app/support/lib/python2.7/site-packages/service_identity-14.0.0-py2.7.egg)
# characteristic: 14.1.0 (/Applications/tahoe.app/support/lib/python2.7/site-packages)
# pyasn1-modules: 0.0.5 (/Applications/tahoe.app/support/lib/python2.7/site-packages/pyasn1_modules-0.0.5-py2.7.egg)

import os, re, subprocess, tempfile, shutil

def test_osx_pkg(pkgfile):
    """ Return on success, raise exception on failure. """

    tmpdir = tempfile.mkdtemp(dir='/tmp')
    # xar -C /tmp/tmpdir -xf PKGNAME
    cmd = ['xar', '-C', tmpdir, '-xf', pkgfile]
    extractit = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    rc = extractit.wait()
    if rc != 0:
        raise Exception("FAIL: xar returned non-zero exit code: %r from command: %r" % (rc, cmd,))

    stderrtxt = extractit.stderr.read()
    if stderrtxt:
        raise Exception("FAIL: xar said something on stderr: %r" % (stderrtxt,))

    # cd /tmp/tmpXXX/tahoe-lafs.pkg
    os.chdir(tmpdir + '/tahoe-lafs.pkg')

    # cat Payload | gunzip -dc | cpio -i
    cat_process = subprocess.Popen(['cat', 'Payload'], stdout=subprocess.PIPE)
    gunzip_process = subprocess.Popen(['gunzip', '-dc'],
                                      stdin=cat_process.stdout,
                                      stdout=subprocess.PIPE)
    cpio_process = subprocess.Popen(['cpio', '-i'],
                                    stdin=gunzip_process.stdout,
                                    stdout=subprocess.PIPE)
    cpio_process.communicate()

    try:
        basedir = os.getcwd()
        cmd = ['bin/tahoe', '--version-and-path']
        callit = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        rc = callit.wait()
        if rc != 0:
            raise Exception("FAIL: '%s' returned non-zero exit code: %r" % (" ".join(cmd), rc))
        stdouttxt = callit.stdout.read()

        PKG_VER_PATH_RE=re.compile("^(\S+): ([^\(]+)\((.+?)\)$", re.UNICODE)

        for mo in PKG_VER_PATH_RE.finditer(stdouttxt):
            if not mo.group(3).startswith(basedir):
                # the following packages are provided by the OS X default installation itself
                if not mo.group(1) in ['zope.interface', 'python', 'platform', 'pyOpenSSL']:
                    raise Exception("FAIL: found package not loaded from basedir (%s); package was: %s" % (basedir, mo.groups(),))
        # success!
    finally:
        shutil.rmtree(tmpdir)


if __name__ == '__main__':
    pkgs = [fn for fn in os.listdir(".") if fn.endswith("-osx.pkg")]
    if len(pkgs) != 1:
        print "ERR: unable to find a single .pkg file:", pkgs
        sys.exit(1)
    print "Testing %s ..." % pkgs[0]
    test_osx_pkg(pkgs[0])
    print "Looks OK!"

