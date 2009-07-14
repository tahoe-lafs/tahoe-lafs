# This script uses hdiutil to attach a dmg (whose name is derived from the
# appname and the version number passed in), asserts that it attached as
# expected, cd's into the mounted filesystem, executes "$appname
# --version-and-path", and checks whether the output of --version-and-path is
# right.

# If all of the paths listed therein are loaded from within the current PWD
# then it exits with code 0.

# If anything goes wrong then it exits with non-zero (failure).  This is to
# check that the Mac OS "DMG" (disk image) package that gets built is correctly
# loading all of its packages from inside the image.

# Here is an example output from --version-and-path:

# allmydata-tahoe: 1.4.1-r3916 (/home/zooko/playground/allmydata/tahoe/trunk/trunk/src), foolscap: 0.4.1 (/usr/local/lib/python2.6/dist-packages/foolscap-0.4.1-py2.6.egg), pycryptopp: 0.5.10 (/home/zooko/playground/allmydata/tahoe/trunk/trunk/support/lib/python2.6/site-packages/pycryptopp-0.5.10-py2.6-linux-x86_64.egg), zfec: 1.4.2 (/usr/local/lib/python2.6/dist-packages/zfec-1.4.2-py2.6-linux-x86_64.egg), Twisted: 8.2.0-r26987 (/usr/local/lib/python2.6/dist-packages/Twisted-8.2.0_r26987-py2.6-linux-x86_64.egg), Nevow: 0.9.32 (/home/zooko/playground/allmydata/tahoe/trunk/trunk/support/lib/python2.6/site-packages/Nevow-0.9.32-py2.6.egg), zope.interface: 3.4.0 (/usr/lib/python2.6/dist-packages), python: 2.6.2 (/usr/bin/python), platform: Linux-Ubuntu_9.04-x86_64-64bit_ELF (None), sqlite: 3.6.10 (unknown), simplejson: 2.0.1 (/usr/local/lib/python2.6/dist-packages/simplejson-2.0.1-py2.6-linux-x86_64.egg), argparse: 0.8.0 (/usr/local/lib/python2.6/dist-packages/argparse-0.8.0-py2.6.egg), pyOpenSSL: 0.7 (/home/zooko/playground/allmydata/tahoe/trunk/trunk/support/lib/python2.6/site-packages/pyOpenSSL-0.7-py2.6-linux-x86_64.egg), pyutil: 1.3.30 (/usr/local/lib/python2.6/dist-packages/pyutil-1.3.30-py2.6.egg), zbase32: 1.1.1 (/usr/local/lib/python2.6/dist-packages/zbase32-1.1.1-py2.6.egg), setuptools: 0.6c12dev (/home/zooko/playground/allmydata/tahoe/trunk/trunk/support/lib/python2.6/site-packages/setuptools-0.6c12dev.egg), pysqlite: 2.4.1 (/usr/lib/python2.6/sqlite3)

import fcntl, os, re, subprocess, time

def test_mac_diskimage(appname, version):
    """ Return True on success, raise exception on failure. """
    assert isinstance(appname, basestring), appname
    assert isinstance(version, basestring), version
    DMGNAME='mac/'+appname+'-'+version+'.dmg'

    cmd = ['hdiutil', 'attach', DMGNAME]
    attachit = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    rc = attachit.wait()
    if rc != 0:
        raise Exception("FAIL: hdiutil returned non-zero exit code: %r from command: %r" % (rc, cmd,))

    stderrtxt = attachit.stderr.read()
    if stderrtxt:
        raise Exception("FAIL: hdiutil said something on stderr: %r" % (stderrtxt,))
    stdouttxt = attachit.stdout.read()
    mo = re.search("^(/[^ ]+)\s+Apple_HFS\s+(/Volumes/.*)$", stdouttxt, re.UNICODE|re.MULTILINE)
    if not mo:
        raise Exception("FAIL: hdiutil said something on stdout that didn't match our expectations: %r" % (stdouttxt,))
    DEV=mo.group(1)
    MOUNTPOINT=mo.group(2)

    callitpid = None
    try:
        basedir = MOUNTPOINT + '/' + appname + '.app/Contents/Resources'

        os.chdir(basedir)

        cmd = ['../MacOS/' + appname, '--version-and-path']
        callit = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        callitpid = callit.pid
        assert callitpid
        deadline = time.time() + 2 # If it takes longer than 2 seconds to do this then it fails.
        while True:
            rc = callit.poll()
            if rc is not None:
                break
            if time.time() > deadline:
                flags = fcntl.fcntl(callit.stdout.fileno(), fcntl.F_GETFL)
                fcntl.fcntl(callit.stdout.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)
                flags = fcntl.fcntl(callit.stderr.fileno(), fcntl.F_GETFL)
                fcntl.fcntl(callit.stderr.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)
                raise Exception("FAIL: it took longer than 2 seconds to invoke $appname --version-and-path. stdout: %r, stderr: %r" % (callit.stdout.read(), callit.stderr.read()))
            time.sleep(0.05)

        if rc != 0:
            raise Exception("FAIL: $appname --version-and-path returned non-zero exit code: %r" % (rc,))

        stdouttxt = callit.stdout.read()

        PKG_VER_PATH_RE=re.compile("(\S+): (\S+) \((.+?)\), ", re.UNICODE)

        for mo in PKG_VER_PATH_RE.finditer(stdouttxt):
            if not mo.group(3).startswith(basedir):
                raise Exception("FAIL: found package not loaded from basedir (%s); package was: %s" % (basedir, mo.groups(),))

        return True # success!
    finally:
        if callitpid:
            os.kill(callitpid, 9)
            os.waitpid(callitpid, 0)
        subprocess.call(['hdiutil', 'detach', '-Force', DEV])
