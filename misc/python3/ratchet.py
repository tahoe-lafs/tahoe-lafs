#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''Ratchet up passing tests, or ratchet down failing tests.

Usage:

    ratchet.py <"up" or "down"> <junitxml file path> <tracking file path>

This script helps when you expect a large test suite to fail spectactularly in
some environment, and you want to gradually improve the situation with minimal
impact to forward development of the same codebase for other environments. The
initial and primary usecase is porting from Python 2 to Python 3.

The idea is to emit JUnit XML from your test runner, and then invoke ratchet.py
to consume this XML output and operate on a so-called "tracking" file. When
ratcheting up passing tests, the tracking file will contain a list of tests,
one per line, that passed. When ratching down, the tracking file contains a
list of failing tests. On each subsequent run, ratchet.py will compare the
prior results in the tracking file with the new results in the XML, and will
report on both welcome and unwelcome changes. It will modify the tracking file
in the case of welcome changes, and therein lies the ratcheting.

The exit codes are:

  0 - no changes observed
  1 - changes observed, whether welcome or unwelcome
  2 - invocation error

Be sure to call as `./ratchet.py` and not `python ratchet.py` if you want to
see our exit code instead of Python's.

If <junitxml file path> does not exist, you'll get a FileNotFoundError:

    >>> _test('up', None, None)                                                # doctest: +ELLIPSIS
    Traceback (most recent call last):
        ...
    FileNotFoundError: ...

If <tracking file path> does not exist, that's fine:

    >>> _test('up', '1', None)
    Some tests not required to pass did:
      c0.t
    Conveniently, they have been added to `<tracking_path>` for you. Perhaps commit that?
    Eep! 0 test(s) were required to pass, but instead 1 did. 🐭

Same if you're ratcheting down:

    >>> _test('down', '1', None)
    All and only tests expected to fail did. 💃

If the test run has the same output as last time, it's all good:

    >>> _test('up', '01001110', '01001110')
    All and only tests required to pass did. 💃

    >>> _test('down', '01001110', '10110001')
    All and only tests expected to fail did. 💃

If there's a welcome change, that's noted:

    >>> _test('up', '0101', '0100')
    Some tests not required to pass did:
      c3.t
    Conveniently, they have been added to `<tracking_path>` for you. Perhaps commit that?
    Eep! 1 test(s) were required to pass, but instead 2 did. 🐭

    >>> _test('down', '0011', '1110')
    Some tests expected to fail didn't:
      c2.t
    Conveniently, they have been removed from `<tracking_path>` for you. Perhaps commit that?
    Eep! 3 test(s) were expected to fail, but instead 2 did. 🐭

And if there is an unwelcome change, that is noted as well:

    >>> _test('up', '1101', '1111')
    Some tests required to pass didn't:
      c2.t
    Eep! 4 test(s) were required to pass, but instead 3 did. 🐭

    >>> _test('down', '0000', '1101')
    Some tests not expected to fail did:
      c2.t
    Eep! 3 test(s) were expected to fail, but instead 4 did. 🐭

And if there are both welcome and unwelcome changes, they are both noted:

    >>> _test('up', '1101', '1011')
    Some tests not required to pass did:
      c1.t
    Conveniently, they have been added to `<tracking_path>` for you. Perhaps commit that?
    Some tests required to pass didn't:
      c2.t
    Eep! 3 test(s) were required to pass, but instead 3 did. 🐭

    >>> _test('down', '0100', '1100')
    Some tests not expected to fail did:
      c2.t
      c3.t
    Some tests expected to fail didn't:
      c1.t
    Conveniently, they have been removed from `<tracking_path>` for you. Perhaps commit that?
    Eep! 2 test(s) were expected to fail, but instead 3 did. 🐭


To test ratchet.py itself:

    python3 -m doctest ratchet.py

'''
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as Etree


class JUnitXMLFile(object):
    '''Represent a file containing test results in JUnit XML format.

    >>> eg = _mktemp_junitxml('0100111')
    >>> results = JUnitXMLFile(eg.name).parse()
    >>> results.failed
    ['c0.t', 'c2.t', 'c3.t']
    >>> results.passed
    ['c1.t', 'c4.t', 'c5.t', 'c6.t']

    '''

    def __init__(self, filepath):
        self.filepath = filepath
        self.failed = []
        self.failed_aggregates = {}
        self.stderr_output = []
        self.passed = []
        self._tree = None

    def parse(self):
        if self._tree:
            raise RuntimeError('already parsed')
        self._tree = Etree.parse(self.filepath)
        for testcase in self._tree.findall('testcase'):
            self.process_testcase(testcase)
        return self

    def process_testcase(self, case):
        key = self.case_key(case)

        # look at children but throw away stderr output
        nonpassing = [c for c in case if not c.tag == 'system-err']
        n = len(nonpassing)
        if n > 1:
            raise RuntimeError(f'multiple results for {key}: {nonpassing}')
        elif n == 1:
            result = nonpassing.pop()
            self.failed.append(key)
            message = result.get('message')
            self.failed_aggregates.setdefault(message, []).append(key)
        else:
            self.passed.append(key)

    @staticmethod
    def case_key(case):
        return f'{case.get("classname")}.{case.get("name")}'

    def report(self, details=False):
        for k, v in sorted(
                self.failed_aggregates.items(),
                key = lambda i: len(i[1]),
                reverse=True):
            print(f'# {k}')
            for t in v:
                print(f' - {t}')


def load_previous_results(txt):
    try:
        previous_results = open(txt).read()
    except FileNotFoundError:
        previous_results = ''
    parsed = set()
    for line in previous_results.splitlines():
        if not line or line.startswith('#'):
            continue
        parsed.add(line)
    return parsed


def print_tests(tests):
    for test in sorted(tests):
        print(' ', test)


def ratchet_up_passing(tracking_path, tests):
    try:
        old = set(open(tracking_path, 'r'))
    except FileNotFoundError:
        old = set()
    new = set(t + '\n' for t in tests)
    merged = sorted(old | new)
    open(tracking_path, 'w+').writelines(merged)


def ratchet_down_failing(tracking_path, tests):
    new = set(t + '\n' for t in tests)
    open(tracking_path, 'w+').writelines(sorted(new))


def main(direction, junitxml_path, tracking_path):
    '''Takes a string indicating which direction to ratchet, "up" or "down,"
    and two paths, one to test-runner output in JUnit XML format, the other to
    a file tracking test results (one test case dotted name per line). Walk the
    former looking for the latter, and react appropriately.

    >>> inp = _mktemp_junitxml('0100111')
    >>> out = _mktemp_tracking('0000000')
    >>> _test_main('up', inp.name, out.name)
    Some tests not required to pass did:
      c1.t
      c4.t
      c5.t
      c6.t
    Conveniently, they have been added to `<tracking_path>` for you. Perhaps commit that?
    Eep! 0 test(s) were required to pass, but instead 4 did. 🐭

    '''

    results = JUnitXMLFile(junitxml_path).parse()

    if tracking_path == '...':
        results.report()
        return

    previous = load_previous_results(tracking_path)
    current = set(results.passed if direction == 'up' else results.failed)

    subjunctive = {'up': 'required to pass', 'down': 'expected to fail'}[direction]
    ratchet = None

    too_many = current - previous
    if too_many:
        print(f'Some tests not {subjunctive} did:')
        print_tests(too_many)
        if direction == 'up':
            # Too many passing tests is good -- let's do more of those!
            ratchet_up_passing(tracking_path, current)
            print(f'Conveniently, they have been added to `{tracking_path}` for you. Perhaps commit that?')

    not_enough = previous - current
    if not_enough:
        print(f'Some tests {subjunctive} didn\'t:')
        print_tests(not_enough)
        if direction == 'down':
            # Not enough failing tests is good -- let's do more of those!
            ratchet_down_failing(tracking_path, current)
            print(f'Conveniently, they have been removed from `{tracking_path}` for you. Perhaps commit that?')

    if too_many or not_enough:
        print(f'Eep! {len(previous)} test(s) were {subjunctive}, but instead {len(current)} did. 🐭')
        return 1

    print(f'All and only tests {subjunctive} did. 💃')
    return 0


# When called as an executable ...

if __name__ == '__main__':
    try:
        direction, junitxml_path, tracking_path = sys.argv[1:4]
        if direction not in ('up', 'down'):
            raise ValueError
    except ValueError:
        doc = '\n'.join(__doc__.splitlines()[:6])
        doc = re.sub(' ratchet.py', f' {sys.argv[0]}', doc)
        print(doc, file=sys.stderr)
        exit_code = 2
    else:
        exit_code = main(direction, junitxml_path, tracking_path)
    sys.exit(exit_code)


# Helpers for when called under doctest ...

def _test(*a):
    return _test_main(*_mk(*a))


def _test_main(direction, junitxml, tracking):
    '''Takes a string 'up' or 'down' and paths to (or open file objects for)
    the JUnit XML and tracking files to use for this test run. Captures and
    emits stdout (slightly modified) for inspection via doctest.'''
    junitxml_path = junitxml.name if hasattr(junitxml, 'name') else junitxml
    tracking_path = tracking.name if hasattr(tracking, 'name') else tracking

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        main(direction, junitxml_path, tracking_path)
    finally:
        sys.stdout.seek(0)
        out = sys.stdout.read()
        out = re.sub('`.*?`', '`<tracking_path>`', out).strip()
        sys.stdout = old_stdout
    print(out)


class _PotentialFile(object):
    '''Represent a file that we are able to create but which doesn't exist yet,
    and which, if we create it, will be automatically torn down when the test
    run is over.'''

    def __init__(self, filename):
        self.d = tempfile.TemporaryDirectory()
        self.name = os.path.join(self.d.name, filename)


def _mk(direction, spec_junitxml, spec_tracking):
    '''Takes a string 'up' or 'down' and two bit strings specifying the state
    of the JUnit XML results file and the tracking file to set up for this test
    case. Returns the direction (unharmed) and two file-ish objects.

    If a spec string is None the corresponding return value will be a
    _PotentialFile object, which has a .name attribute (like a true file
    object) that points to a file that does not exist, but could.

    The reason not to simply return the path in all cases is that the file
    objects are actually temporary file objects that destroy the underlying
    file when they go out of scope, and we want to keep the underlying file
    around until the end of the test run.'''

    if None not in(spec_junitxml, spec_tracking):
        if len(spec_junitxml) != len(spec_tracking):
            raise ValueError('if both given, must be the same length: `{spec_junitxml}` and `{spec_tracking}`')
    if spec_junitxml is None:
        junitxml_fp = _PotentialFile('results.xml')
    else:
        junitxml_fp = _mktemp_junitxml(spec_junitxml)
    if spec_tracking is None:
        tracking_fp = _PotentialFile('tracking')
    else:
        tracking_fp = _mktemp_tracking(spec_tracking)
    return direction, junitxml_fp, tracking_fp


def _mktemp_junitxml(spec):
    '''Test helper to generate a raw JUnit XML file.

    >>> fp = _mktemp_junitxml('00101')
    >>> open(fp.name).read()[:11]
    '<testsuite>'

    '''
    fp = tempfile.NamedTemporaryFile()
    fp.write(b'<testsuite>')

    passed = '''\
<testcase classname="c{i}" name="t"></testcase>
'''
    failed = '''\
<testcase classname="c{i}" name="t">
<failure>Traceback (most recent call last):
  File "/foo/bar/baz/buz.py", line 1, in &lt;module>
NameError: name 'heck' is not defined
</failure>
</testcase>
'''

    i = 0
    for c in spec:
        if c == '0':
            out = failed
        elif c == '1':
            out = passed
        else:
            raise ValueError(f'bad c: `{c}`')
        fp.write(out.format(i=i).encode('utf8'))
        i += 1

    fp.write(b'</testsuite>')
    fp.flush()
    return fp


def _mktemp_tracking(spec):
    '''Test helper to prefabricate a tracking file.

    >>> fp = _mktemp_tracking('01101')
    >>> print(open(fp.name).read()[:-1])
    c1.t
    c2.t
    c4.t

    '''
    fp = tempfile.NamedTemporaryFile()

    i = 0
    for c in spec:
        if c == '0':
            pass
        elif c == '1':
            fp.write(f'c{i}.t\n'.encode('utf8'))
        else:
            raise ValueError(f'bad c: `{c}`')
        i += 1

    fp.flush()
    return fp
