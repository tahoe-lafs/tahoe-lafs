#!/usr/bin/env python
# This script generates a table of dependencies in HTML format on stdout.
# It expects to be run in the tahoe-lafs-dep-eggs directory.

import re, os, sys

extensions = ('.egg', '.tar.bz2', '.tar.gz', '.exe')
platform_aliases = [('i686','x86'), ('i386','x86'), ('i86pc','x86'), ('win32','windows-x86'),
                    ('win-amd64','windows-x86_64'), ('amd64','x86_64')]
FILENAME_RE  = re.compile(r'([a-zA-Z_0-9\.]*)-([0-9\.a-vx-z_]*)(-py[0-9\.]*)?(-.*)?')
FILENAME_RE2 = re.compile(r'([a-zA-Z_0-9\.]*)-([0-9\.a-vx-z_]*)(win32|win-amd64)?(-py[0-9\.]*)?')

matrix = {}
pkgs = set()
platform_dependent_pkgs = set()
python_versions = set()

depdir = '.'
if len(sys.argv) > 1:
    depdir = sys.argv[1]

filenames = os.listdir(depdir)

def add(d, k, v):
    if k in d:
        d[k] += [v]
    else:
        d[k] = [v]

for fname in filenames:
    for ext in extensions:
        if fname.endswith(ext):
            m = FILENAME_RE.match(fname[:-len(ext)])
            try:
                pkg       = m.group(1)
                pythonver = (m.group(3) or '-py')[3:]
                platform  = (m.group(4) or '-')[1:]
            except (IndexError, AttributeError, TypeError):
                continue

            if not pythonver:
                m = FILENAME_RE2.match(fname[:-len(ext)])
                if m.group(3):
                    try:
                        platform  = m.group(3)
                        pythonver = (m.group(4) or '-py')[3:]
                    except (IndexError, AttributeError, TypeError):
                        continue

            for (alias, replacement) in platform_aliases:
                if platform.endswith(alias):
                    platform = platform[:-len(alias)] + replacement
                    break

            pkgs.add(pkg)
            if platform:
                platform_dependent_pkgs.add(pkg)
            if pythonver not in matrix:
                python_versions.add(pythonver)
                matrix[pythonver] = {}
            add(matrix[pythonver], platform, (pkg, fname))
            break

platform_independent_pkgs = pkgs - platform_dependent_pkgs

width = 100 / (len(platform_independent_pkgs) + 1)

print '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" "http://www.w3.org/TR/html4/loose.dtd">'
print '<html>'
print '<head>'
print '  <meta http-equiv="Content-Type" content="text/html;charset=us-ascii">'
print '  <title>Software packages that Tahoe-LAFS depends on</title>'
print '</head>'
print '<body>'
print '<h2>Software packages that Tahoe-LAFS depends on</h2>'
print
for pyver in reversed(sorted(python_versions)):
    if pyver:
        print '<p>Packages for Python %s that have compiled C/C++ code:</p>' % (pyver,)
        print '<table border="1">'
        print '  <tr>'
        print '    <th style="background-color: #FFFFD0" width="%d%%">&nbsp;Platform&nbsp;</th>' % (width,)
        for pkg in sorted(platform_dependent_pkgs):
            print '    <th style="background-color:#FFE8FF;" width="%d%%">&nbsp;%s&nbsp;</th>' % (width, pkg)
        print '  </tr>'

        first = True
        for platform in sorted(matrix[pyver]):
            row_files = sorted(matrix[pyver][platform])
            style1 = first and 'border-top: 2px solid #000000; background-color: #FFFFF0' or 'background-color: #FFFFF0'
            style2 = first and 'border-top: 2px solid #000000' or ''
            print '  <tr>'
            print '    <td style="%s">&nbsp;%s&nbsp;</td>' % (style1, platform,)
            for pkg in sorted(platform_dependent_pkgs):
                files = [n for (p, n) in row_files if pkg == p]
                if pkg == 'pywin32' and not platform.startswith('windows'):
                    print '    <td style="border: 0; text-align: center; %s"> n/a </td>' % (style2,)
                else:
                    print '    <td style="%s">&nbsp;%s</td>' % (style2,
                            '<br>&nbsp;'.join(['<a href="%s">%s</a>' % (f, f) for f in files]))
            print '  </tr>'
            first = False

    print '</table>'
    print

print '<p>Packages that are platform-independent or source-only:</p>'
print '<table border="1">'
print '  <tr>'
print '    <th style="background-color:#FFFFD0;">&nbsp;Package&nbsp;</th>'
print '    <th style="background-color:#FFE8FF;">&nbsp;All Python versions&nbsp;</th>'
print '  </tr>'

style1 = 'border-top: 2px solid #000000; background-color:#FFFFF0;'
style2 = 'border-top: 2px solid #000000;'
m = matrix['']['']
for pkg in sorted(platform_independent_pkgs):
    print '  <tr>'
    print '    <th style="%s">&nbsp;%s&nbsp;</th>' % (style1, pkg)
    files = [n for (p, n) in m if pkg == p]
    print '    <td style="%s">&nbsp;%s</td>' % (style2, '<br>&nbsp;'.join(['<a href="%s">%s</a>' % (f, f) for f in files]))
    print '  </tr>'

print '</table>'

# The document does validate, but not when it is included at the bottom of a directory listing.
#print '<hr>'
#print '<a href="http://validator.w3.org/check?uri=referer" target="_blank"><img border="0" src="http://www.w3.org/Icons/valid-html401-blue" alt="Valid HTML 4.01 Transitional" height="31" width="88"></a>'
print '</body></html>'
