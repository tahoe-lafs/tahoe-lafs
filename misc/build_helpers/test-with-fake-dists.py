#!/usr/bin/env python

import StringIO, glob, os, platform, shutil, subprocess, sys, tarfile, zipfile
import pkg_resources

def test():
    # We put a fake "pycryptopp-0.5.24.egg" package and a fake
    # "pycryptopp-9.9.99.tar.gz" into a directory, but the latter is
    # booby-trapped so it will raise an exception when you try to build
    # it.

    # Then we run "python setup.py test -s
    # buildtest.test_with_fake_dist", which imports pycryptopp
    # and passes if pycryptopp.__version__ == '0.5.24'.

    # (If building succeeded -- meaning that you didn't try to build the
    # booby-trapped 9.9.99 -- but pycryptopp.__version__ != '0.5.24' then
    # that means a different version of pycryptopp was already installed
    # so neither of the two fake pycryptopp packages were needed. In that
    # case this test should be treated as a "skip" -- the functionality
    # under test can't be exercised on the current system.)

    # The goal is to turn red if the build system tries to build the
    # source dist when it could have used the binary dist.

    # (Note that for this test to make sense, tahoe-lafs needs to be
    # asking for a version of pycryptopp which can be satisfied by either
    # 0.5.24 or 0.5.25. At the time of this writing it requires >= 0.5.20
    # on x86 and >= 0.5.14 on other architectures.)

    fake_distdir = 'tahoe-deps'
    fake_distname = "pycryptopp"
    fake_sdistversion = "9.9.99"
    fake_bdistversion = "0.5.24"
    sdist_setup = "raise Exception('Aha I caught you trying to build me. I am a fake pycryptopp 9.9.99 sdist and you should be satisfied with a bdist.')"

    testsuite = "buildtest.test_build_with_fake_dist"

    dist_dirname = os.path.join(os.getcwd(), fake_distdir)

    try:
        os.makedirs(dist_dirname)
    except OSError:
        # probably already exists
        pass

    bdist_egg_name = os.path.join(dist_dirname, '%s-%s-py%s.%s-%s.egg' % (fake_distname, fake_bdistversion, platform.python_version_tuple()[0], platform.python_version_tuple()[1], pkg_resources.get_supported_platform()))
    try:
        bdist_egg = zipfile.ZipFile(bdist_egg_name, 'w')
        bdist_egg.writestr('pycryptopp/__init__.py', '__version__ = "%s"\n' % (fake_bdistversion,))
        bdist_egg.close()

        sdist_name = os.path.join(dist_dirname, '%s-%s.tar' % (fake_distname, fake_sdistversion))
        sdist = tarfile.open(sdist_name, 'w:gz')
        sdist.errorlevel =2
        tarinfo = tarfile.TarInfo('setup.py')
        tarinfo.errorlevel =2
        tarinfo.size = len(sdist_setup)
        sdist.addfile(tarinfo, StringIO.StringIO(sdist_setup))
        sdist.close()

        sys.exit(subprocess.call([sys.executable, "setup.py", "-v", "test", "-s", testsuite], env=os.environ))
    finally:
        os.remove(bdist_egg_name)
        os.remove(sdist_name)
        cleanup()

def cleanup():
    for path, subdnames, fnames in os.walk('.'):
        for fdname in subdnames + fnames:
            if fdname.startswith('pycryptopp-0.5.24'):
                shutil.rmtree(os.path.join(path, fdname))

if __name__ == '__main__':
    test()
