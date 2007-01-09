#! /usr/bin/python

import os, sys, pickle

def longest_common_prefix(elements):
    if not elements:
        return ""
    prefix = elements[0]
    for e in elements:
        prefix = longest_common_prefix_2(prefix, e)
    return prefix
def longest_common_prefix_2(a, b):
    maxlen = min(len(a), len(b))
    for i in range(maxlen, 0, -1):
        if a[:i] == b[:i]:
            return a[:i]
    return ""

## def write_el(r2):
##     filenames = sorted(r2.keys())
##     out = open(".figleaf.el", "w")
##     out.write("(setq figleaf-results '(\n")
##     for f in filenames:
##         linenumbers = r2[f]
##         out.write(' ("%s" (%s))\n' % (f, " ".join([str(ln)
##                                                    for ln in linenumbers])))
##     out.write(" ))\n")
##     out.close()

def write_el(r2, source):
    filenames = sorted(r2.keys())
    out = open(".figleaf.el", "w")
    out.write("""
;; This is an elisp-readable form of the figleaf coverage data. It defines a
;; single top-level hash table in which the load-path-relative filename (like
;; allmydata/download.py) is the key, and the value is a three-element list.
;; The first element of this list is a list of line numbers that represent
;; actual code. The second is a list of line numbers for lines which got used
;; during the unit test. The third is a list of line numbers for code lines
;; that were not covered (since 'code' and 'covered' start as sets, this last
;; list is equal to 'code - covered').

""")
    out.write("(let ((results (make-hash-table :test 'equal)))\n")
    for f in filenames:
        covered_linenumbers = r2[f]
        code_linenumbers = source[f]
        uncovered_code = code_linenumbers - covered_linenumbers
        out.write(" (puthash \"%s\" '((%s) (%s) (%s)) results)\n"
                  % (f,
                     " ".join([str(ln) for ln in sorted(code_linenumbers)]),
                     " ".join([str(ln) for ln in sorted(covered_linenumbers)]),
                     " ".join([str(ln) for ln in sorted(uncovered_code)]),
                     ))
    out.write(" results)\n")
    out.close()

#import figleaf
from allmydata.util import figleaf

def examine_source(filename):
    f = open(filename, "r")
    lines = figleaf.get_lines(f)
    f.close()
    return lines

def main():
    results = pickle.load(open(sys.argv[1], "rb"))
    import_prefix = os.path.abspath(sys.argv[2])
    if not import_prefix.endswith("/"):
        import_prefix = import_prefix + "/"
    plen = len(import_prefix)

    r2 = {}
    source = {}
    filenames = sorted(results.keys())
    here = os.getcwd()
    for f in filenames:
        if f.startswith(import_prefix):
            short = f[plen:]
            r2[short] = results[f]
            source[short] = examine_source(f)
    write_el(r2, source)

if __name__ == '__main__':
    main()


