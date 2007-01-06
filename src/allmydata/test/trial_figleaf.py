
"""A Trial IReporter plugin that gathers figleaf code-coverage information.

Once this plugin is installed, trial can be invoked with one of two new
--reporter options:

  trial --reporter=verbose-figleaf ARGS
  trial --reporter-bwverbose-figleaf ARGS

Once such a test run has finished, there will be a .figleaf file in the
top-level directory. This file can be turned into a directory of .html files
(with index.html as the starting point) by running:

 figleaf2html -d OUTPUTDIR [-x EXCLUDEFILE]

Figleaf thinks of everyting in terms of absolute filenames rather than
modules. The EXCLUDEFILE may be necessary to keep it from providing reports
on non-Code-Under-Test files that live in unusual locations. In particular,
if you use extra PYTHONPATH arguments to point at some alternate version of
an upstream library (like Twisted), or if something like debian's
python-support puts symlinks to .py files in sys.path but not the .py files
themselves, figleaf will present coverage information on both of these. The
EXCLUDEFILE option might help to inhibit these.

Other figleaf problems:

 the annotated code files are written to BASENAME(file).html, which results
 in collisions between similarly-named source files.

 The line-wise coverage information isn't quite right. Blank lines are
 counted as unreached code, lambdas aren't quite right, and some multiline
 comments (docstrings?) aren't quite right.

"""

from twisted.trial.reporter import TreeReporter, VerboseTextReporter

# These plugins are registered via twisted/plugins/allmydata_trial.py . See
# the notes there for an explanation of how that works.



# Reporters don't really get told about the suite starting and stopping.

# The Reporter class is imported before the test classes are.

# The test classes are imported before the Reporter is created. To get
# control earlier than that requires modifying twisted/scripts/trial.py .

# Then Reporter.__init__ is called.

# Then tests run, calling things like write() and addSuccess(). Each test is
# framed by a startTest/stopTest call.

# Then the results are emitted, calling things like printErrors,
# printSummary, and wasSuccessful.

# So for code-coverage (not including import), start in __init__ and finish
# in printSummary. To include import, we have to start in our own import and
# finish in printSummary.

from allmydata.util import figleaf
# don't cover py_ecc, it takes forever
from allmydata.py_ecc import rs_code
import os
py_ecc_dir = os.path.realpath(os.path.dirname(rs_code.__file__))
figleaf.start(ignore_prefixes=[py_ecc_dir])


class FigleafReporter(TreeReporter):
    def __init__(self, *args, **kwargs):
        TreeReporter.__init__(self, *args, **kwargs)

    def printSummary(self):
        figleaf.stop()
        figleaf.write_coverage(".figleaf")
        print "Figleaf results written to .figleaf"
        return TreeReporter.printSummary(self)

class FigleafTextReporter(VerboseTextReporter):
    def __init__(self, *args, **kwargs):
        VerboseTextReporter.__init__(self, *args, **kwargs)

    def printSummary(self):
        figleaf.stop()
        figleaf.write_coverage(".figleaf")
        print "Figleaf results written to .figleaf"
        return VerboseTextReporter.printSummary(self)

class not_FigleafReporter(object):
    # this class, used as a reporter on a fully-passing test suite, doesn't
    # trigger exceptions. So it is a guide to what methods are invoked on a
    # Reporter.
    def __init__(self, *args, **kwargs):
        print "FIGLEAF HERE"
        self.r = TreeReporter(*args, **kwargs)
        self.shouldStop = self.r.shouldStop
        self.separator = self.r.separator
        self.testsRun = self.r.testsRun
        self._starting2 = False

    def write(self, *args):
        if not self._starting2:
            self._starting2 = True
            print "FIRST WRITE"
        return self.r.write(*args)

    def startTest(self, *args, **kwargs):
        return self.r.startTest(*args, **kwargs)

    def stopTest(self, *args, **kwargs):
        return self.r.stopTest(*args, **kwargs)

    def addSuccess(self, *args, **kwargs):
        return self.r.addSuccess(*args, **kwargs)

    def printErrors(self, *args, **kwargs):
        return self.r.printErrors(*args, **kwargs)

    def writeln(self, *args, **kwargs):
        return self.r.writeln(*args, **kwargs)

    def printSummary(self, *args, **kwargs):
        print "PRINT SUMMARY"
        return self.r.printSummary(*args, **kwargs)

    def wasSuccessful(self, *args, **kwargs):
        return self.r.wasSuccessful(*args, **kwargs)

