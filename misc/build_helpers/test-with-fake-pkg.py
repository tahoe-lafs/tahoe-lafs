#!/usr/bin/env python

# We put a fake "pycryptopp-0.5.17" package on the PYTHONPATH so that
# the build system thinks pycryptopp-0.5.17 is already installed. Then
# we execute run_trial.py. If the build system is too naive/greedy
# about finding dependencies, it will latch onto the
# "pycryptopp-0.5.17" and then will be unable to satisfy the
# requirement (from _auto_deps.py) for pycryptopp >= 0.5.20. This is
# currently happening on trunk, see #1190. So with trunk, running
# test-with-fake-pkg.py shows a failure, but with the ticket1190
# branch, test-with-fake-pkg.py succeeds.

import glob, os, subprocess, sys

fakepkgname = "pycryptopp"
fakepkgversion = "0.5.17"
# testsuite = "allmydata.test.test_cli"
testsuite = "allmydata.test.test_base62"

pkgdirname = os.path.join(os.getcwd(), '%s-%s.egg' % (fakepkgname, fakepkgversion))

try:
    os.makedirs(pkgdirname)
except OSError:
    # probably already exists
    pass
os.chdir('src')
trial=os.path.join(os.getcwd(), '..', 'misc', 'build_helpers', 'run_trial.py')
os.environ['PATH']=os.getcwd()+os.pathsep+os.environ['PATH']
eggs = [os.path.realpath(p) for p in glob.glob(os.path.join('..', '*.egg'))]
os.environ['PYTHONPATH']=os.pathsep+pkgdirname+os.pathsep+os.pathsep.join(eggs)+os.pathsep+os.environ.get('PYTHONPATH','')
sys.exit(subprocess.call([sys.executable, trial, testsuite], env=os.environ))
