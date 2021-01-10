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
    given,
)

from hypothesis.strategies import (
    lists,
    text,
)

from subprocess import (
    check_call,
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

    @given(lists(text(max_size=4), max_size=4))
    def test_argv_values(self, argv):
        """
        ``get_argv`` returns a list representing the result of tokenizing the
        "command line" argument string provided to Windows processes.
        """
        save_argv = FilePath(self.mktemp())
        saved_argv_path = FilePath(self.mktemp())
        with open(save_argv.path, "wt") as f:
            f.write(
                """
                import sys
                import json
                with open({!r}, "wt") as f:
                    f.write(json.dumps(sys.argv))
                """.format(saved_argv_path.path),
            )
        check_call([
            executable,
            save_argv.path,
        ] + argv)

        with open(saved_argv_path.path, "rt") as f:
            saved_argv = load(f)

        self.assertThat(
            argv,
            Equals(saved_argv),
        )
