"""
The following code is valid in Python 2:

for x in my_dict.keys():
    if something(x):
        del my_dict[x]

But broken in Python 3.

One solution is:

for x in list(my_dict.keys()):
    if something(x):
        del my_dict[x]

Some but not all code in Tahoe has been changed to that. In other cases, the code was left unchanged since there was no `del`.

However, some mistakes may have slept through.

To help catch cases that were incorrectly ported, this script runs futurize on all ported modules, which should convert it into the `list()` form.
You can then look at git diffs to see if any of the impacted would be buggy without the newly added `list()`.
"""

import os
from subprocess import check_call

from allmydata.util import _python3


def fix_potential_issue():
    for module in _python3.PORTED_MODULES + _python3.PORTED_TEST_MODULES:
        filename = "src/" + module.replace(".", "/") + ".py"
        if not os.path.exists(filename):
            # Package, probably
            filename = "src/" + module.replace(".", "/") + "/__init__.py"
        check_call(["futurize", "-f", "lib2to3.fixes.fix_dict", "-w", filename])
    print(
        "All loops converted. Check diff to see if there are any that need to be commitedd."
    )


if __name__ == "__main__":
    fix_potential_issue()
