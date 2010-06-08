#! /usr/bin/env python

from optparse import OptionParser
import os, sys

from darcsver import darcsvermodule, __version__

try:
    EXE_NAME=os.path.basename(sys.argv[0])
except:
    EXE_NAME="darcsver"

def main():
    parser = OptionParser(usage="Usage: %prog [options] [pkgname [verfilename]]",
                          version="%prog " + str(__version__),
                          prog=EXE_NAME)
    parser.add_option("-q", "--quiet", default=False, action="store_true",
                      help="Be quiet, do the job without any output.")
    parser.add_option("--count-all-patches", "--revision-number", default=False,
                      action="store_true", dest="count_all_patches",
                      help="By default %s counts the number of patches since the "
                           "most recent release tag. With this option, it counts "
                           "all the patches in the repository." % EXE_NAME)

    options, args = parser.parse_args()

    if args:
        pkgname = args.pop(0)
    else:
        pkgname = os.path.basename(os.getcwd())
        if not options.quiet:
            print "%s: You didn't pass a pkg-name on the command-line, so I'm going to take the name of the current working directory: \"%s\"" % (EXE_NAME, pkgname,)

    if args:
        verfilename = args.pop(0)
    else:
        verfilename = os.path.join(pkgname, "_version.py")
        if not options.quiet:
            print "%s: You didn't pass a verfilename on the command-line, so I'm going to build one from the name of the package: \"%s\"" % (EXE_NAME, verfilename,)

    (rc, newverstr) = darcsvermodule.update(pkgname=pkgname, verfilename=verfilename, revision_number=options.count_all_patches, quiet=options.quiet, EXE_NAME=EXE_NAME)
    return rc

if __name__ == "__main__":
    rc = main()
    sys.exit(rc)
