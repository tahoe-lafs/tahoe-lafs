from __future__ import print_function

import sys, os, io, re
from twisted.internet import reactor, protocol, task, defer
from twisted.python.procutils import which
from twisted.python import usage

# run the command with python's deprecation warnings turned on, capturing
# stderr. When done, scan stderr for warnings, write them to a separate
# logfile (so the buildbot can see them), and return rc=1 if there were any.

class Options(usage.Options):
    optParameters = [
        ["warnings", None, None, "file to write warnings into at end of test run"],
        ["package", None, None, "Python package to which to restrict warning collection"]
        ]

    def parseArgs(self, command, *args):
        self["command"] = command
        self["args"] = list(args)

    description = """Run as:
python run-deprecations.py [--warnings=STDERRFILE] [--package=PYTHONPACKAGE ] COMMAND ARGS..
"""

class RunPP(protocol.ProcessProtocol):
    def outReceived(self, data):
        self.stdout.write(data)
        sys.stdout.write(str(data, sys.stdout.encoding))
    def errReceived(self, data):
        self.stderr.write(data)
        sys.stderr.write(str(data, sys.stdout.encoding))
    def processEnded(self, reason):
        signal = reason.value.signal
        rc = reason.value.exitCode
        self.d.callback((signal, rc))


def make_matcher(options):
    """
    Make a function that matches a line with a relevant deprecation.

    A deprecation warning line looks something like this::

      somepath/foo/bar/baz.py:43: DeprecationWarning: Foo is deprecated, try bar instead.

    Sadly there is no guarantee warnings begin at the beginning of a line
    since they are written to output without coordination with whatever other
    Python code is running in the process.

    :return: A one-argument callable that accepts a string and returns
        ``True`` if it contains an interesting warning and ``False``
        otherwise.
    """
    pattern = r".*\.py[oc]?:\d+:" # (Pending)?DeprecationWarning: .*"
    if options["package"]:
        pattern = r".*/{}/".format(
            re.escape(options["package"]),
        ) + pattern
    expression = re.compile(pattern)
    def match(line):
        return expression.match(line) is not None
    return match


@defer.inlineCallbacks
def run_command(main):
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

    pp = RunPP()
    pp.d = defer.Deferred()
    pp.stdout = io.BytesIO()
    pp.stderr = io.BytesIO()
    reactor.spawnProcess(pp, exe, [exe] + config["args"], env=None)
    (signal, rc) = yield pp.d

    match = make_matcher(config)

    # maintain ordering, but ignore duplicates (for some reason, either the
    # 'warnings' module or twisted.python.deprecate isn't quashing them)
    already = set()
    warnings = []
    def add(line):
        if line in already:
            return
        already.add(line)
        warnings.append(line)

    pp.stdout.seek(0)
    for line in pp.stdout.readlines():
        line = str(line, sys.stdout.encoding)
        if match(line):
            add(line) # includes newline

    pp.stderr.seek(0)
    for line in pp.stderr.readlines():
        line = str(line, sys.stdout.encoding)
        if match(line):
            add(line)

    if warnings:
        if config["warnings"]:
            with open(config["warnings"], "w") as f:
                print("".join(warnings), file=f)
        print("ERROR: %d deprecation warnings found" % len(warnings))
        sys.exit(1)

    print("no deprecation warnings")
    if signal:
        sys.exit(signal)
    sys.exit(rc)


task.react(run_command)
