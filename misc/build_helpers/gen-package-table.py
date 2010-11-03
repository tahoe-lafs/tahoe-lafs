#!python
# This script generates a table of dependencies in HTML format on stdout.
# It expects to be run in the tahoe-lafs-dep-eggs directory.

import re, os, sys

extensions = ('.egg', '.tar.bz2', '.tar.gz', '.exe')
platform_aliases = [('i686','x86'), ('i386','x86'), ('i86pc','x86'), ('win32','windows-x86'),
                    ('win-amd64','windows-x86_64'), ('amd64','x86_64')]
python_versions = ((2,4), (2,5), (2,6), (2,7))
FILENAME_RE  = re.compile(r'([a-zA-Z_0-9]*)-([0-9\.]*)(-py[0-9\.]*)?(-.*)?')
FILENAME_RE2 = re.compile(r'([a-zA-Z_0-9]*)-([0-9\.]*)(win32|win-amd64)?(-py[0-9\.]*)?')

matrix = {}

depdir = '.'
if len(sys.argv) >= 1:
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

            add(matrix, (pkg, platform), (pythonver, fname))
            break

print '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" "http://www.w3.org/TR/html4/loose.dtd">'
print '<html>'
print '<head>'
print '  <meta http-equiv="Content-Type" content="text/html;charset=us-ascii">'
print '  <title>Software packages that Tahoe-LAFS depends on</title>'
print '</head>'
print '<body>'
print '<h2>Software packages that Tahoe-LAFS depends on</h2>'
print
print '<p>Packages that have compiled C/C++ code:</p>'
print '<table border="1">'
print '  <tr>'
print '    <th colspan=2 style="background-color: #FFFFD0">&nbsp;Package&nbsp;</th>'
for pyver in python_versions:
    print '    <th style="background-color:#FFE8FF;">&nbsp;Python %d.%d&nbsp;</th>' % pyver
print '  </tr>'

platform_dependent_pkgs = set()

last_pkg = None
for (pkg, platform) in sorted(matrix):
    if platform:
        platform_dependent_pkgs.add(pkg)
        row_files = sorted(matrix[(pkg, platform)])
        style1 = pkg != last_pkg and 'border-top: 2px solid #000000; background-color: #FFFFF0' or 'border: 0;'
        style2 = pkg != last_pkg and 'border-top: 2px solid #000000; background-color: #FFFFF0' or 'background-color: #FFFFF0;'
        style3 = pkg != last_pkg and 'border-top: 2px solid #000000;' or ''
        print '  <tr>'
        print '    <th style="%s">&nbsp;%s&nbsp;</th>' % (style1, pkg != last_pkg and pkg or '',)
        print '    <td style="%s">&nbsp;%s&nbsp;</td>' % (style2, platform,)
        for pyver in python_versions:
            files = [n for (v, n) in row_files if v == '%d.%d' % pyver]
            print '    <td style="%s">&nbsp;%s</td>' % (style3,
                    '<br>&nbsp;'.join(['<a href="%s">%s</a>' % (f, f) for f in files]))
        print '  </tr>'
        last_pkg = pkg

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
for (pkg, platform) in sorted(matrix):
    if pkg not in platform_dependent_pkgs:
        print '  <tr>'
        print '    <th style="%s">&nbsp;%s&nbsp;</th>' % (style1, pkg)
        files = [n for (v, n) in sorted(matrix[(pkg, platform)]) if not v]
        print '    <td style="%s">&nbsp;%s</td>' % (style2, '<br>&nbsp;'.join(['<a href="%s">%s</a>' % (f, f) for f in files]))
        print '  </tr>'

print '</table>'

# The document does validate, but not when it is included at the bottom of a directory listing.
#print '<hr>'
#print '<a href="http://validator.w3.org/check?uri=referer" target="_blank"><img border="0" src="http://www.w3.org/Icons/valid-html401-blue" alt="Valid HTML 4.01 Transitional" height="31" width="88"></a>'
print '</body></html>'
