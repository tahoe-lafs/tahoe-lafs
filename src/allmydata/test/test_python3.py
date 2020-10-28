"""
Tests related to the Python 3 porting effort itself.

This module has been ported to Python 3.
"""
from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2, native_str
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from twisted.python.modules import (
    getModule,
)
from twisted.trial.unittest import (
    SynchronousTestCase,
)

from allmydata.util._python3 import PORTED_MODULES, PORTED_TEST_MODULES


class Python3PortingEffortTests(SynchronousTestCase):

    def test_finished_porting(self):
        """
        Tahoe-LAFS has been ported to Python 3.

        Once
        https://tahoe-lafs.org/trac/tahoe-lafs/milestone/Support%20Python%203
        is completed this test should pass (and can be deleted!).
        """
        tahoe_lafs_module_names = set(all_module_names("allmydata"))
        ported_names = set(ported_module_names())
        self.assertEqual(
            tahoe_lafs_module_names - ported_names,
            set(),
            "Some unported modules remain: {}".format(
                unported_report(
                    tahoe_lafs_module_names,
                    ported_names,
                ),
            ),
        )
    test_finished_porting.todo = native_str(
        "https://tahoe-lafs.org/trac/tahoe-lafs/milestone/Support%20Python%203 should be completed",
    )

    def test_ported_modules_exist(self):
        """
        All modules listed as ported exist and belong to Tahoe-LAFS.
        """
        tahoe_lafs_module_names = set(all_module_names("allmydata"))
        ported_names = set(ported_module_names())
        unknown = ported_names - tahoe_lafs_module_names
        self.assertEqual(
            unknown,
            set(),
            "Some supposedly-ported modules weren't found: {}.".format(sorted(unknown)),
        )

    def test_ported_modules_distinct(self):
        """
        The ported modules list doesn't contain duplicates.
        """
        ported_names_list = ported_module_names()
        ported_names_list.sort()
        ported_names_set = set(ported_names_list)
        ported_names_unique_list = list(ported_names_set)
        ported_names_unique_list.sort()
        self.assertEqual(
            ported_names_list,
            ported_names_unique_list,
        )


def all_module_names(toplevel):
    """
    :param unicode toplevel: The name of a top-level Python package.

    :return iterator[unicode]: An iterator of ``unicode`` giving the names of
        all modules within the given top-level Python package.
    """
    allmydata = getModule(toplevel)
    for module in allmydata.walkModules():
        name = module.name
        if PY2:
            name = name.decode("utf-8")
        yield name


def ported_module_names():
    """
    :return list[unicode]: A ``list`` of ``unicode`` giving the names of
        Tahoe-LAFS modules which have been ported to Python 3.
    """
    return PORTED_MODULES + PORTED_TEST_MODULES


def unported_report(tahoe_lafs_module_names, ported_names):
    return """
Ported files: {} / {}
Ported lines: {} / {}
""".format(
    len(ported_names),
    len(tahoe_lafs_module_names),
    sum(map(count_lines, ported_names)),
    sum(map(count_lines, tahoe_lafs_module_names)),
)

def count_lines(module_name):
    module = getModule(module_name)
    try:
        source = module.filePath.getContent()
    except Exception as e:
        print((module_name, e))
        return 0
    lines = source.splitlines()
    nonblank = [_f for _f in lines if _f]
    return len(nonblank)
