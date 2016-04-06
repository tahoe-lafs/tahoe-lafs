import sys, os, subprocess
from twisted.python.procutils import which
from twisted.python import usage

# run the command with python's deprecation warnings turned on, capturing
# stderr. When done, scan stderr for warnings, write them to a separate
# logfile (so the buildbot can see them), and return rc=1 if there were any.

class Options(usage.Options):
    optParameters = [
        ["stderr", None, None, "file to write stderr into at end of test run"],
        ]

    def parseArgs(self, command, *args):
        self["command"] = command
        self["args"] = list(args)

    description = """Run as:
PYTHONWARNINGS=default::DeprecationWarning python run-deprecations.py [--stderr=STDERRFILE]  COMMAND ARGS..
"""

config = Options()
config.parseOptions()


command = config["command"]
if "/" in command:
    # don't search
    exe = command
else:
    executables = which(command)
    if not executables:
        raise ValueError("unable to find '%s' in PATH (%s)" %
                         (command, os.environ.get("PATH")))
    exe = executables[0]

pw = os.environ.get("PYTHONWARNINGS")
DDW = "default::DeprecationWarning"
if pw != DDW:
    print "note: $PYTHONWARNINGS is '%s', not the expected %s" % (pw, DDW)

print "note: stderr is being captured, and will be emitted at the end"

# stdout goes directly to the parent, so test progress can be watched in real
# time. But subprocess.Popen() doesn't give us any good way of seeing it
p = subprocess.Popen([exe] + config["args"], stderr=subprocess.PIPE)
stderr = p.communicate()[1]
rc = p.returncode
count = 0

if config["stderr"]:
    with open(config["stderr"], "wb") as f:
        print >>f, stderr,

if stderr:
    print >>sys.stderr, "--"
    print >>sys.stderr, "Captured stderr follows:"
    for line in stderr.splitlines():
        if "DeprecationWarning" in line:
            count += 1
        print >>sys.stderr, line
    print >>sys.stderr, "--"

if count:
    print "ERROR: %d deprecation warnings found" % count
    sys.exit(1)
print "no deprecation warnings"
sys.exit(rc)
