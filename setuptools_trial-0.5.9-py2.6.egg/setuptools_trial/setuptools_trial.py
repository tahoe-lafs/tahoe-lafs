import sys

from setuptools.command import test


class TrialTest(test.test):
    """
    Twisted Trial setuptools command
    """

    user_options = test.test.user_options + [
        ('rterrors', 'e', "Realtime errors: print out tracebacks as soon as they occur."),
        ('debug-stacktraces', 'B', "Report Deferred creation and callback stack traces."),
        ('coverage','c', "Report coverage data."),
        ('reactor=','r', "which reactor to use"),
        ('reporter=', None, "Customize Trial's output with a Reporter plugin."),
        ('until-failure','u', "Repeat test until it fails."),
    ]

    boolean_options = ['coverage', 'debug-stacktraces', 'rterrors']

    def initialize_options(self):
        test.test.initialize_options(self)
        self.coverage = None
        self.debug_stacktraces = None
        self.reactor = None
        self.reporter = None
        self.rterrors = None
        self.until_failure = None

    def finalize_options(self):
        if self.test_suite is None:
            if self.test_module is None:
                self.test_suite = self.distribution.test_suite
            else:
                self.test_suite = self.test_module
        elif self.test_module:
            raise DistutilsOptionError(
                "You may specify a module or a suite, but not both"
            )

        self.test_args = self.test_suite

    def run_tests(self):
        # We do the import from Twisted inside the function instead of the top
        # of the file because since Twisted is a setup_requires, we can't
        # assume that Twisted will be installed on the user's system prior
        # to using Tahoe, so if we don't do the import here, then importing
        # from this plugin will fail.
        from twisted.scripts import trial

        # Handle parsing the trial options passed through the setuptools
        # trial command.
        cmd_options = []
        if self.reactor is not None:
            cmd_options.extend(['--reactor', self.reactor])
        else:
            # Cygwin requires the poll reactor to work at all.  Linux requires the poll reactor
            # to avoid twisted bug #3218.  In general, the poll reactor is better than the
            # select reactor, but it is not available on all platforms.  According to exarkun on
            # IRC, it is available but buggy on some versions of Mac OS X, so just because you
            # can install it doesn't mean we want to use it on every platform.
            # Unfortunately this leads to this error with some combinations of tools:
            # twisted.python.usage.UsageError: The specified reactor cannot be used, failed with error: reactor already installed.
            if sys.platform in ("cygwin"):
                cmd_options.extend(['--reactor', 'poll'])
        if self.reporter is not None:
            cmd_options.extend(['--reporter', self.reporter])
        if self.rterrors is not None:
            cmd_options.append('--rterrors')
        if self.debug_stacktraces is not None:
            cmd_options.append('--debug-stacktraces')
        config = trial.Options()
        config.parseOptions(cmd_options)


        args = self.test_args
        if type(args) == str:
            args = [args,]

        config['tests'] = args

        if self.coverage:
            config.opt_coverage()

        trial._initialDebugSetup(config)
        trialRunner = trial._makeRunner(config)
        suite = trial._getSuite(config)

        # run the tests
        if self.until_failure:
            test_result = trialRunner.runUntilFailure(suite)
        else:
            test_result = trialRunner.run(suite)

        # write coverage data
        if config.tracer:
            sys.settrace(None)
            results = config.tracer.results()
            results.write_results(show_missing=1, summary=False,
                                  coverdir=config.coverdir)

        if test_result.wasSuccessful():
            sys.exit(0) # success
        else:
            sys.exit(1) # failure
