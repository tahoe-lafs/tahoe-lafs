
import os, shutil
from subprocess import check_call

# Install tahoe into a new virtualenv, move aside the source tree, run a test
# with the installed tahoe. This ensures that the installed code isn't
# depending upon anything from the source tree. Requires 'pip' and
# 'virtualenv' to be installed, and enough compilers/libraries (libffi-dev)
# to enable 'pip install'.

# This runs a lot faster if you've cached wheels first. Edit ~/.pip/pip.conf
# to have [global] wheel-dir=find-links=/HOME/.pip/wheels, then run 'pip
# wheel .' from the tahoe tree.

assert os.path.exists("Tahoe.home"), "Run this from the top of the source tree."
VE = "test-pip-install-virtualenv"

print "creating virtualenv.."
if os.path.exists(VE):
    shutil.rmtree(VE)
check_call(["virtualenv", VE])
print "running 'pip install .' from virtualenv.."
check_call(["%s/bin/pip" % VE, "install", "."])
try:
    print "moving src/ out of the away"
    os.rename("src", "src-disabled-by-test-pip-install")
    print "running 'trial allmydata.test.test_web' from virtualenv.."
    rc = check_call(["%s/bin/trial" % VE, "allmydata.test.test_web"])
finally:
    print "moving src/ back"
    os.rename("src-disabled-by-test-pip-install", "src")
