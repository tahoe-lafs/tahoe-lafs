#! /usr/bin/env python
"""
figleaf is another tool to trace code coverage (yes, in Python ;).

figleaf uses the sys.settrace hook to record which statements are
executed by the CPython interpreter; this record can then be saved
into a file, or otherwise communicated back to a reporting script.

figleaf differs from the gold standard of coverage tools
('coverage.py') in several ways.  First and foremost, figleaf uses the
same criterion for "interesting" lines of code as the sys.settrace
function, which obviates some of the complexity in coverage.py (but
does mean that your "loc" count goes down).  Second, figleaf does not
record code executed in the Python standard library, which results in
a significant speedup.  And third, the format in which the coverage
format is saved is very simple and easy to work with.

You might want to use figleaf if you're recording coverage from
multiple types of tests and need to aggregate the coverage in
interesting ways, and/or control when coverage is recorded.
coverage.py is a better choice for command-line execution, and its
reporting is a fair bit nicer.

Command line usage: ::

  figleaf.py <python file to execute> <args to python file>

The figleaf output is saved into the file '.figleaf', which is an
*aggregate* of coverage reports from all figleaf runs from this
directory.  '.figleaf' contains a pickled dictionary of sets; the keys
are source code filenames, and the sets contain all line numbers
executed by the Python interpreter. See the docs or command-line
programs in bin/ for more information.

High level API: ::

 * ``start(ignore_lib=True)`` -- start recording code coverage.
 * ``stop()``                 -- stop recording code coverage.
 * ``get_trace_obj()``        -- return the (singleton) trace object.
 * ``get_info()``             -- get the coverage dictionary

Classes & functions worth knowing about, i.e. a lower level API:

 * ``get_lines(fp)`` -- return the set of interesting lines in the fp.
 * ``combine_coverage(d1, d2)`` -- combine coverage info from two dicts.
 * ``read_coverage(filename)`` -- load the coverage dictionary
 * ``write_coverage(filename)`` -- write the coverage out.
 * ``annotate_coverage(...)`` -- annotate a Python file with its coverage info.

Known problems:

 -- module docstrings are *covered* but not found.

AUTHOR: C. Titus Brown, titus@idyll.org

'figleaf' is Copyright (C) 2006.  It will be released under the BSD license.
"""
import sys
import os
import threading
from cPickle import dump, load

### import builtin sets if in > 2.4, otherwise use 'sets' module.
# we require 2.4 or later
assert set


from token import tok_name, NEWLINE, STRING, INDENT, DEDENT, COLON
import parser, types, symbol

def get_token_name(x):
    """
    Utility to help pretty-print AST symbols/Python tokens.
    """
    if symbol.sym_name.has_key(x):
        return symbol.sym_name[x]
    return tok_name.get(x, '-')

class LineGrabber:
    """
    Count 'interesting' lines of Python in source files, where
    'interesting' is defined as 'lines that could possibly be
    executed'.

    @CTB this badly needs to be refactored... once I have automated
    tests ;)
    """
    def __init__(self, fp):
        """
        Count lines of code in 'fp'.
        """
        self.lines = set()

        self.ast = parser.suite(fp.read())
        self.tree = parser.ast2tuple(self.ast, True)

        self.find_terminal_nodes(self.tree)

    def find_terminal_nodes(self, tup):
        """
        Recursively eat an AST in tuple form, finding the first line
        number for "interesting" code.
        """
        (sym, rest) = tup[0], tup[1:]

        line_nos = set()
        if type(rest[0]) == types.TupleType:  ### node

            for x in rest:
                token_line_no = self.find_terminal_nodes(x)
                if token_line_no is not None:
                    line_nos.add(token_line_no)

            if symbol.sym_name[sym] in ('stmt', 'suite', 'lambdef',
                                        'except_clause') and line_nos:
                # store the line number that this statement started at
                self.lines.add(min(line_nos))
            elif symbol.sym_name[sym] in ('if_stmt',):
                # add all lines under this
                self.lines.update(line_nos)
            elif symbol.sym_name[sym] in ('global_stmt',): # IGNORE
                return
            else:
                if line_nos:
                    return min(line_nos)

        else:                               ### leaf
            if sym not in (NEWLINE, STRING, INDENT, DEDENT, COLON) and \
               tup[1] != 'else':
                return tup[2]
            return None

    def pretty_print(self, tup=None, indent=0):
        """
        Pretty print the AST.
        """
        if tup is None:
            tup = self.tree

        s = tup[1]

        if type(s) == types.TupleType:
            print ' '*indent, get_token_name(tup[0])
            for x in tup[1:]:
                self.pretty_print(x, indent+1)
        else:
            print ' '*indent, get_token_name(tup[0]), tup[1:]

def get_lines(fp):
    """
    Return the set of interesting lines in the source code read from
    this file handle.
    """
    l = LineGrabber(fp)
    return l.lines

class CodeTracer:
    """
    Basic code coverage tracking, using sys.settrace.
    """
    def __init__(self, ignore_prefix=None):
        self.c = {}
        self.started = False
        self.ignore_prefix = ignore_prefix

    def start(self):
        """
        Start recording.
        """
        if not self.started:
            self.started = True

            sys.settrace(self.g)
            if hasattr(threading, 'settrace'):
                threading.settrace(self.g)

    def stop(self):
        if self.started:
            sys.settrace(None)
            if hasattr(threading, 'settrace'):
                threading.settrace(None)

            self.started = False

    def g(self, f, e, a):
        """
        global trace function.
        """
        if e is 'call':
            if self.ignore_prefix and \
                   f.f_code.co_filename.startswith(self.ignore_prefix):
                return

            return self.t

    def t(self, f, e, a):
        """
        local trace function.
        """

        if e is 'line':
            self.c[(f.f_code.co_filename, f.f_lineno)] = 1
        return self.t

    def clear(self):
        """
        wipe out coverage info
        """

        self.c = {}

    def gather_files(self):
        """
        Return the dictionary of lines of executed code; the dict
        contains items (k, v), where 'k' is the filename and 'v'
        is a set of line numbers.
        """
        files = {}
        for (filename, line) in self.c.keys():
            d = files.get(filename, set())
            d.add(line)
            files[filename] = d

        return files

def combine_coverage(d1, d2):
    """
    Given two coverage dictionaries, combine the recorded coverage
    and return a new dictionary.
    """
    keys = set(d1.keys())
    keys.update(set(d2.keys()))

    new_d = {}
    for k in keys:
        v = d1.get(k, set())
        v2 = d2.get(k, set())

        s = set(v)
        s.update(v2)
        new_d[k] = s

    return new_d

def write_coverage(filename, combine=True):
    """
    Write the current coverage info out to the given filename.  If
    'combine' is false, destroy any previously recorded coverage info.
    """
    if _t is None:
        return

    d = _t.gather_files()

    # combine?
    if combine:
        old = {}
        fp = None
        try:
            fp = open(filename)
        except IOError:
            pass

        if fp:
            old = load(fp)
            fp.close()
            d = combine_coverage(d, old)

    # ok, save.
    outfp = open(filename, 'w')
    try:
        dump(d, outfp)
    finally:
        outfp.close()

def read_coverage(filename):
    """
    Read a coverage dictionary in from the given file.
    """
    fp = open(filename)
    try:
        d = load(fp)
    finally:
        fp.close()

    return d

def annotate_coverage(in_fp, out_fp, covered, all_lines,
                      mark_possible_lines=False):
    """
    A simple example coverage annotator that outputs text.
    """
    for i, line in enumerate(in_fp):
        i = i + 1

        if i in covered:
            symbol = '>'
        elif i in all_lines:
            symbol = '!'
        else:
            symbol = ' '

        symbol2 = ''
        if mark_possible_lines:
            symbol2 = ' '
            if i in all_lines:
                symbol2 = '-'

        out_fp.write('%s%s %s' % (symbol, symbol2, line,))

#######################

#
# singleton functions/top-level API
#

_t = None

def start(ignore_python_lib=True):
    """
    Start tracing code coverage.  If 'ignore_python_lib' is True,
    ignore all files that live below the same directory as the 'os'
    module.
    """
    global _t
    if _t is None:
        ignore_path = None
        if ignore_python_lib:
            ignore_path = os.path.realpath(os.path.dirname(os.__file__))
        _t = CodeTracer(ignore_path)

    _t.start()

def stop():
    """
    Stop tracing code coverage.
    """
    global _t
    if _t is not None:
        _t.stop()

def get_trace_obj():
    """
    Return the (singleton) trace object, if it exists.
    """
    return _t

def get_info():
    """
    Get the coverage dictionary from the trace object.
    """
    if _t:
        return _t.gather_files()

#############

def display_ast():
    l = LineGrabber(open(sys.argv[1]))
    l.pretty_print()

def main():
    """
    Execute the given Python file with coverage, making it look like it is
    __main__.
    """
    ignore_pylibs = False

    def print_help():
        print 'Usage: figleaf [-i] <program-to-profile> <program-options>'
        print ''
        print 'Options:'
        print '  -i             Ignore Python standard libraries when calculating coverage'

    args = sys.argv[1:]

    if len(args) < 1:
        print_help()
        raise SystemExit()
    elif len(args) > 2 and args[0] == '-i':
        ignore_pylibs = True

        ## Make sure to strip off the -i or --ignore-python-libs option if it exists
        args = args[1:]

    ## Reset system args so that the subsequently exec'd file can read from sys.argv
    sys.argv = args

    sys.path[0] = os.path.dirname(args[0])

    cwd = os.getcwd()

    start(ignore_pylibs)        # START code coverage

    import __main__
    try:
        execfile(args[0], __main__.__dict__)
    finally:
        stop()                          # STOP code coverage

        write_coverage(os.path.join(cwd, '.figleaf'))
