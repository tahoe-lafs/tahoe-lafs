# -*- coding: utf-8 -*-
# Tahoe-LAFS -- secure, distributed storage grid
#
# Copyright © 2020 The Tahoe-LAFS Software Foundation
#
# This file is part of Tahoe-LAFS.
#
# See the docs/about.rst file for licensing information.

"""
Tests for the ``allmydata.windows``.
"""

from __future__ import division
from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from sys import (
    executable,
)
from json import (
    load,
)
from textwrap import (
    dedent,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.python.runtime import (
    platform,
)

from testtools import (
    skipUnless,
)

from testtools.matchers import (
    MatchesAll,
    AllMatch,
    IsInstance,
    Equals,
)

from hypothesis import (
    HealthCheck,
    settings,
    given,
    note,
)

from hypothesis.strategies import (
    lists,
    text,
    characters,
)

from .common import (
    PIPE,
    Popen,
    SyncTestCase,
)

slow_settings = settings(
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,

    # Reduce the number of examples required to consider the test a success.
    # The default is 100.  Launching a process is expensive so we'll try to do
    # it as few times as we can get away with.  To maintain good coverage,
    # we'll try to pass as much data to each process as we can so we're still
    # covering a good portion of the space.
    max_examples=10,
)

@skipUnless(platform.isWindows(), "get_argv is Windows-only")
@skipUnless(PY2, "Not used on Python 3.")
class GetArgvTests(SyncTestCase):
    """
    Tests for ``get_argv``.
    """
    def test_get_argv_return_type(self):
        """
        ``get_argv`` returns a list of unicode strings
        """
        # Hide the ``allmydata.windows.fixups.get_argv`` import here so it
        # doesn't cause failures on non-Windows platforms.
        from ..windows.fixups import (
            get_argv,
        )
        argv = get_argv()

        # We don't know what this process's command line was so we just make
        # structural assertions here.
        self.assertThat(
            argv,
            MatchesAll(
                IsInstance(list),
                AllMatch(IsInstance(str)),
            ),
        )

    # This test runs a child process.  This is unavoidably slow and variable.
    # Disable the two time-based Hypothesis health checks.
    @slow_settings
    @given(
        lists(
            text(
                alphabet=characters(
                    blacklist_categories=('Cs',),
                    # Windows CommandLine is a null-terminated string,
                    # analogous to POSIX exec* arguments.  So exclude nul from
                    # our generated arguments.
                    blacklist_characters=('\x00',),
                ),
                min_size=10,
                max_size=20,
            ),
            min_size=10,
            max_size=20,
        ),
    )
    def test_argv_values(self, argv):
        """
        ``get_argv`` returns a list representing the result of tokenizing the
        "command line" argument string provided to Windows processes.
        """
        working_path = FilePath(self.mktemp())
        working_path.makedirs()
        save_argv_path = working_path.child("script.py")
        saved_argv_path = working_path.child("data.json")
        with open(save_argv_path.path, "wt") as f:
            # A simple program to save argv to a file.  Using the file saves
            # us having to figure out how to reliably get non-ASCII back over
            # stdio which may pose an independent set of challenges.  At least
            # file I/O is relatively simple and well-understood.
            f.write(dedent(
                """
                from allmydata.windows.fixups import (
                    get_argv,
                )
                import json
                with open({!r}, "wt") as f:
                    f.write(json.dumps(get_argv()))
                """.format(saved_argv_path.path)),
            )
        argv = [executable.decode("utf-8"), save_argv_path.path] + argv
        p = Popen(argv, stdin=PIPE, stdout=PIPE, stderr=PIPE)
        p.stdin.close()
        stdout = p.stdout.read()
        stderr = p.stderr.read()
        returncode = p.wait()

        note("stdout: {!r}".format(stdout))
        note("stderr: {!r}".format(stderr))

        self.assertThat(
            returncode,
            Equals(0),
        )
        with open(saved_argv_path.path, "rt") as f:
            saved_argv = load(f)

        self.assertThat(
            saved_argv,
            Equals(argv),
        )


@skipUnless(platform.isWindows(), "intended for Windows-only codepaths")
@skipUnless(PY2, "Not used on Python 3.")
class UnicodeOutputTests(SyncTestCase):
    """
    Tests for writing unicode to stdout and stderr.
    """
    @slow_settings
    @given(characters(), characters())
    def test_write_non_ascii(self, stdout_char, stderr_char):
        """
        Non-ASCII unicode characters can be written to stdout and stderr with
        automatic UTF-8 encoding.
        """
        working_path = FilePath(self.mktemp())
        working_path.makedirs()
        script = working_path.child("script.py")
        script.setContent(dedent(
            """
            from future.utils import PY2
            if PY2:
                from future.builtins import chr

            from allmydata.windows.fixups import initialize
            initialize()

            # XXX A shortcoming of the monkey-patch approach is that you'd
            # better not import stdout or stderr before you call initialize.
            from sys import argv, stdout, stderr

            stdout.write(chr(int(argv[1])))
            stdout.close()
            stderr.write(chr(int(argv[2])))
            stderr.close()
            """
        ))
        p = Popen([
            executable,
            script.path,
            str(ord(stdout_char)),
            str(ord(stderr_char)),
        ], stdout=PIPE, stderr=PIPE)
        stdout = p.stdout.read().decode("utf-8").replace("\r\n", "\n")
        stderr = p.stderr.read().decode("utf-8").replace("\r\n", "\n")
        returncode = p.wait()

        self.assertThat(
            (stdout, stderr, returncode),
            Equals((
                stdout_char,
                stderr_char,
                0,
            )),
        )
