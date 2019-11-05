"""
Tests related to the Python 3 porting effort itself.
"""

from pkg_resources import (
    resource_stream,
)

from twisted.python.modules import (
    getModule,
)
from twisted.trial.unittest import (
    SynchronousTestCase,
)


class Python3PortingEffortTests(SynchronousTestCase):
    def test_finished_porting(self):
        """
        Tahoe-LAFS has been ported to Python 3.
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
    test_finished_porting.todo = "https://tahoe-lafs.org/trac/tahoe-lafs/milestone/Support%20Python%203 should be completed"

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
        yield module.name.decode("utf-8")


def ported_module_names():
    """
    :return list[unicode]: A ``set`` of ``unicode`` giving the names of
        Tahoe-LAFS modules which have been ported to Python 3.
    """
    return resource_stream(
        "allmydata",
        u"ported-modules.txt",
    ).read().splitlines()


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
        print(module_name, e)
        return 0
    lines = source.splitlines()
    nonblank = filter(None, lines)
    return len(nonblank)
