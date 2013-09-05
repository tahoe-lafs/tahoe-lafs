#! /usr/bin/python

import os, sys, compiler, traceback
from compiler.ast import Node, For, AssName, Name, Lambda, Function


def check_source(source):
    return check_thing(compiler.parse, source)

def check_file(path):
    return check_thing(compiler.parseFile, path)

def check_thing(parser, thing):
    try:
        ast = parser(thing)
    except SyntaxError, e:
        return [e]
    else:
        results = []
        check_ast(ast, results)
        return results

def check_ast(ast, results):
    """Check a node outside a 'for' loop."""
    if isinstance(ast, For):
        check_for(ast, results)
    else:
        for child in ast.getChildNodes():
            if isinstance(ast, Node):
                check_ast(child, results)

def check_for(ast, results):
    """Check a particular outer 'for' loop."""

    declared = {}  # maps name to lineno of declaration
    nested = set()
    collect_declared_and_nested(ast, declared, nested)

    # For each nested function...
    for funcnode in nested:
        # Check for captured variables in this function.
        captured = set()
        collect_captured(funcnode, declared, captured)
        for name in captured:
            # We want to report the outermost capturing function
            # (since that is where the workaround will need to be
            # added), and the variable declaration. Just one report
            # per capturing function per variable will do.
            results.append(make_result(funcnode, name, declared[name]))

        # Check each node in the function body in case it
        # contains another 'for' loop.
        childnodes = funcnode.getChildNodes()[len(funcnode.defaults):]
        for child in childnodes:
            check_ast(funcnode, results)

def collect_declared_and_nested(ast, declared, nested):
    """
    Collect the names declared in this 'for' loop, not including
    names declared in nested functions. Also collect the nodes of
    functions that are nested one level deep.
    """
    if isinstance(ast, AssName):
        declared[ast.name] = ast.lineno
    else:
        childnodes = ast.getChildNodes()
        if isinstance(ast, (Lambda, Function)):
            nested.add(ast)

            # The default argument expressions are "outside" the
            # function, even though they are children of the
            # Lambda or Function node.
            childnodes = childnodes[:len(ast.defaults)]

        for child in childnodes:
            if isinstance(ast, Node):
                collect_declared_and_nested(child, declared, nested)

def collect_captured(ast, declared, captured):
    """Collect any captured variables that are also in declared."""
    if isinstance(ast, Name):
        if ast.name in declared:
            captured.add(ast.name)
    else:
        childnodes = ast.getChildNodes()

        if isinstance(ast, (Lambda, Function)):
            # Formal parameters of the function are excluded from
            # captures we care about in subnodes of the function body.
            declared = declared.copy()
            for argname in ast.argnames:
                if argname in declared:
                    del declared[argname]

            for child in childnodes[len(ast.defaults):]:
                collect_captured(child, declared, captured)

            # The default argument expressions are "outside" the
            # function, even though they are children of the
            # Lambda or Function node.
            childnodes = childnodes[:len(ast.defaults)]

        for child in childnodes:
            if isinstance(ast, Node):
                collect_captured(child, declared, captured)


def make_result(funcnode, var_name, var_lineno):
    if hasattr(funcnode, 'name'):
        func_name = 'function %r' % (funcnode.name,)
    else:
        func_name = '<lambda>'
    return (funcnode.lineno, func_name, var_name, var_lineno)

def report(out, path, results):
    for r in results:
        if isinstance(r, SyntaxError):
            print >>out, path + (" NOT ANALYSED due to syntax error: %s" % r)
        else:
            print >>out, path + (":%r %s captures %r declared at line %d" % r)

def check(sources, out):
    class Counts:
        n = 0
        processed_files = 0
        suspect_files = 0
    counts = Counts()

    def _process(path):
        results = check_file(path)
        report(out, path, results)
        counts.n += len(results)
        counts.processed_files += 1
        if len(results) > 0:
            counts.suspect_files += 1

    for source in sources:
        print >>out, "Checking %s..." % (source,)
        if os.path.isfile(source):
            _process(source)
        else:
            for (dirpath, dirnames, filenames) in os.walk(source):
                for fn in filenames:
                    (basename, ext) = os.path.splitext(fn)
                    if ext == '.py':
                        _process(os.path.join(dirpath, fn))

    print >>out, ("%d suspiciously captured variables in %d out of %d files"
                  % (counts.n, counts.suspect_files, counts.processed_files))
    return counts.n


sources = ['src']
if len(sys.argv) > 1:
    sources = sys.argv[1:]
if check(sources, sys.stderr) > 0:
    sys.exit(1)


# TODO: self-tests
