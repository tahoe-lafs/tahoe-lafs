#!/usr/bin/env python

from allmydata.util import fileutil

import re, shutil, sys

R=re.compile("^#! */usr/bin/python *$")
for fname in sys.argv[1:]:
    inf = open(fname, "rU")
    rntf = fileutil.ReopenableNamedTemporaryFile()
    outf = open(rntf.name, "w")
    first = True
    for l in inf:
        if first and R.search(l):
            outf.write("#!/usr/bin/env python\n")
        else:
            outf.write(l)
        first = False
    outf.close()

    try:
        shutil.move(rntf.name, fname)
    except EnvironmentError:
        # Couldn't atomically overwrite, so just hope that this process doesn't die
        # and the target file doesn't get recreated in between the following two
        # operations:
        shutil.move(fname, fname + ".bak")
        shutil.move(rntf.name, fname)

        fileutil.remove_if_possible(fname + ".bak")
