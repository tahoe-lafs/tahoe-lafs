#!/usr/bin/env python
# -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-
# -*- indent-tabs-mode: nil -*-

import StringIO, os, platform, shutil, subprocess, sys, tarfile, zipfile, time
import pkg_resources

def test():
    # We put a "fakedependency-0.9.9.egg" package and a
    # "fakedependency-1.0.0.tar.gz" into a directory, but the former is
    # booby-trapped so it will raise an exception when you try to import it.
    #
    # Then we run
    #
    #   python setup.py --fakedependency -v test -s buildtest.test_build_with_fake_dist
    #
    # which requires "fakedependency >= 1.0.0", imports fakedependency
    # and passes if fakedependency.__version__ == '1.0.0'.
    #
    # The goal is to turn red if the build system tries to use the
    # source dist when it could have used the binary dist.
    #
    # Note that for this test to make sense, Tahoe-LAFS needs to be asking
    # for a version of fakedependency which can be satisfied by 1.0.0.
    # The --fakedependency option to setup.py arranges that.

    fake_distdir = 'tahoe-deps'
    fake_distname = "fakedependency"
    fake_sdistversion = "1.0.0"
    fake_bdistversion = "0.9.9"

    bdist_init = "raise Exception('Aha I caught you trying to import me. I am a fakedependency 0.9.9 package and you should not be satisfied with something < 1.0.0.')"
    sdist_setup = "import distutils.core\ndistutils.core.setup(name='fakedependency', version='1.0.0', packages=['fakedependency'])"
    sdist_init = "__version__ = '%s'" % (fake_sdistversion,)

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
        bdist_egg.writestr('fakedependency/__init__.py', bdist_init)
        bdist_egg.close()

        sdist_name = os.path.join(dist_dirname, '%s-%s.tar' % (fake_distname, fake_sdistversion))
        sdist = tarfile.open(sdist_name, 'w:gz')
        sdist.errorlevel = 2
        tarinfo = tarfile.TarInfo('setup.py')
        tarinfo.errorlevel = 2
        tarinfo.mtime = time.time()
        tarinfo.size = len(sdist_setup)
        sdist.addfile(tarinfo, StringIO.StringIO(sdist_setup))
        tarinfo = tarfile.TarInfo('fakedependency/__init__.py')
        tarinfo.errorlevel = 2
        tarinfo.mtime = time.time()
        tarinfo.size = len(sdist_init)
        sdist.addfile(tarinfo, StringIO.StringIO(sdist_init))
        sdist.close()

        sys.exit(subprocess.call([sys.executable, "setup.py", "--fakedependency", "-v", "test", "-s", testsuite],
                                 env=os.environ))
    finally:
        os.remove(bdist_egg_name)
        os.remove(sdist_name)
        cleanup()

def cleanup():
    egg_info = os.path.join('src', 'allmydata_tahoe.egg-info')
    bin_tahoe = os.path.join('bin', 'tahoe')
    bin_tahoe_pyscript = os.path.join('bin', 'tahoe.pyscript')

    if os.path.exists('build'):
        shutil.rmtree('build')
    if os.path.exists('support'):
        shutil.rmtree('support')
    if os.path.exists(egg_info):
        shutil.rmtree(egg_info)
    if os.path.exists(bin_tahoe):
        os.remove(bin_tahoe)
    if os.path.exists(bin_tahoe_pyscript):
        os.remove(bin_tahoe_pyscript)

if __name__ == '__main__':
    test()
