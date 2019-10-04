# Copyright 2004, 2009 Toby Dickenson
# Copyright 2014-2015 Aaron Gallagher
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject
# to the following conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import collections
import functools
import json
import os
import modulefinder
import sys
import tempfile

from twisted.python import reflect


class mymf(modulefinder.ModuleFinder):
    def __init__(self, *args, **kwargs):
        self._depgraph = collections.defaultdict(set)
        self._types = {}
        self._last_caller = None
        modulefinder.ModuleFinder.__init__(self, *args, **kwargs)

    def import_hook(self, name, caller=None, fromlist=None, level=None):
        old_last_caller = self._last_caller
        try:
            self._last_caller = caller
            return modulefinder.ModuleFinder.import_hook(
                self, name, caller, fromlist)
        finally:
            self._last_caller = old_last_caller

    def import_module(self, partnam, fqname, parent):
        if partnam.endswith('_py3'):
            return None
        r = modulefinder.ModuleFinder.import_module(
            self, partnam, fqname, parent)
        last_caller = self._last_caller
        if r is not None and 'allmydata' in r.__name__:
            if last_caller is None or last_caller.__name__ == '__main__':
                self._depgraph[fqname]
            else:
                self._depgraph[last_caller.__name__].add(fqname)
        return r

    def load_module(self, fqname, fp, pathname, (suffix, mode, type)):
        r = modulefinder.ModuleFinder.load_module(
            self, fqname, fp, pathname, (suffix, mode, type))
        if r is not None:
            self._types[r.__name__] = type
        return r

    def as_json(self):
        return {
            'depgraph': {
                name: dict.fromkeys(deps, 1)
                for name, deps in self._depgraph.iteritems()},
            'types': self._types,
        }


json_dump = functools.partial(
    json.dump, indent=4, separators=(',', ': '), sort_keys=True)


def main(target):
    mf = mymf(sys.path[:], 0, [])

    moduleNames = []
    for path, dirnames, filenames in os.walk(os.path.join(target, 'src', 'allmydata')):
        if 'test' in dirnames:
            dirnames.remove('test')
        for filename in filenames:
            if not filename.endswith('.py'):
                continue
            if filename in ('setup.py',):
                continue
            if '-' in filename:
                # a script like update-documentation.py
                continue
            if filename != '__init__.py':
                filepath = os.path.join(path, filename)
            else:
                filepath = path
            moduleNames.append(reflect.filenameToModuleName(filepath))

    with tempfile.NamedTemporaryFile() as tmpfile:
        for moduleName in moduleNames:
            tmpfile.write('import %s\n' % moduleName)
        tmpfile.flush()
        mf.run_script(tmpfile.name)

    with open('tahoe-deps.json', 'wb') as outfile:
        json_dump(mf.as_json(), outfile)
        outfile.write('\n')

    ported_modules_path = os.path.join(target, "misc", "python3", "ported-modules.txt")
    with open(ported_modules_path) as ported_modules:
        port_status = dict.fromkeys((line.strip() for line in ported_modules), "ported")
    with open('tahoe-ported.json', 'wb') as outfile:
        json_dump(port_status, outfile)
        outfile.write('\n')


if __name__ == '__main__':
    main(*sys.argv[1:])
