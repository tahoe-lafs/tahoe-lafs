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
    SyncTestCase,
)

from ..windows.fixups import (
    get_argv,
)

@skipUnless(platform.isWindows(), "get_argv is Windows-only")
class GetArgvTests(SyncTestCase):
    """
    Tests for ``get_argv``.
    """
    def test_get_argv_return_type(self):
        """
        ``get_argv`` returns a list of unicode strings
        """
        # We don't know what this process's command line was so we just make
        # structural assertions here.
        argv = get_argv()
        self.assertThat(
            argv,
            MatchesAll(
                IsInstance(list),
                AllMatch(IsInstance(str)),
            ),
        )

    @settings(
        # This test runs a child process.  This is unavoidably slow and
        # variable.  Disable the two time-based Hypothesis health checks.
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
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
                min_size=1,
                max_size=4,
            ),
            min_size=1,
            max_size=4,
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
        # Python 2.7 doesn't have good options for launching a process with
        # non-ASCII in its command line.
        from ._win_subprocess import (
            Popen
        )
        from subprocess import (
            PIPE,
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
