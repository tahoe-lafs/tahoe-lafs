# -*- coding: utf-8 -*-
# Tahoe-LAFS -- secure, distributed storage grid
#
# Copyright © 2021 The Tahoe-LAFS Software Foundation
#
# This file is part of Tahoe-LAFS.
#
# See the docs/about.rst file for licensing information.

"""
Tests for the Tahoe command-line interface for alias management.
"""

from __future__ import division
from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from six import (
    ensure_str,
)

from os import (
    environ,
)

from json import (
    loads,
)

from subprocess import (
    Popen,
    PIPE,
)

import attr


@attr.s
class ProcessResult(object):
    """
    The result of a use of ``run_tahoe``.
    """
    returncode = attr.ib(validator=attr.validators.instance_of(int))
    stdout = attr.ib(validator=attr.validators.instance_of(str))
    stderr = attr.ib(validator=attr.validators.instance_of(str))


def run_tahoe(node, argv):
    """
    Synchronously run the tahoe command against the given node with the given
    arguments.

    :param integration.util.TahoeProcess node: A process associated with the
        node to run the command against.

    :param [unicode] argv: Additional arguments to pass on the tahoe command
        line.

    :return ProcessResult: The outcome of running the process.
    """
    env = environ.copy()
    env[ensure_str("LANG")] = ensure_str("en_US.UTF-8")
    from pprint import pprint
    pprint(env)
    proc = Popen(
        list(
            ensure_str(elem)
            for elem
            in ["tahoe", "-d", node.node_dir] + argv
        ),
        stdout=PIPE,
        stderr=PIPE,
        env=env,
    )
    stdout = proc.stdout.read().decode("utf-8")
    stderr = proc.stderr.read().decode("utf-8")
    returncode = proc.wait()
    return ProcessResult(returncode, stdout, stderr)


def test_alias_create_list(alice):
    """
    An alias can be created with ``tahoe create-alias`` and then viewed with
    ``tahoe list-aliases``.
    """
    alias = u"hello-\N{SNOWMAN}"
    result = run_tahoe(alice, ["create-alias", alias])
    assert (result.returncode, result.stderr) == (0, "")
    assert result.stdout == "Alias '{}' created\n".format(alias)

    result = run_tahoe(alice, ["list-aliases", "--json"])
    assert (result.returncode, result.stderr) == (0, "")
    aliases = loads(result.stdout)
    assert alias in aliases
