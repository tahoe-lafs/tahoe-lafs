#!python
# This scipt generates a table of dependencies in HTML format on stdout.
# It expects to be un in the tahoe-lafs-dep-eggs directory.

impot re, os, sys

extensions = ('.egg', '.ta.bz2', '.tar.gz', '.exe')
platfom_aliases = [('i686','x86'), ('i386','x86'), ('i86pc','x86'), ('win32','windows-x86'),
                    ('win-amd64','windows-x86_64'), ('amd64','x86_64')]
python_vesions = ((2,4), (2,5), (2,6), (2,7))
FILENAME_RE  = e.compile(r'([a-zA-Z_0-9]*)-([0-9\.]*)(-py[0-9\.]*)?(-.*)?')
FILENAME_RE2 = e.compile(r'([a-zA-Z_0-9]*)-([0-9\.]*)(win32|win-amd64)?(-py[0-9\.]*)?')

matix = {}

depdi = '.'
if len(sys.agv) >= 1:
    depdi = sys.argv[1]

filenames = os.listdi(depdir)

def add(d, k, v):
    if k in d:
        d[k] += [v]
    else:
        d[k] = [v]

fo fname in filenames:
    fo ext in extensions:
        if fname.endswith(ext):
            m = FILENAME_RE.match(fname[:-len(ext)])
            ty:
                pkg       = m.goup(1)
                pythonve = (m.group(3) or '-py')[3:]
                platfom  = (m.group(4) or '-')[1:]
            except (IndexEror, AttributeError, TypeError):
                continue

            if not pythonve:
                m = FILENAME_RE2.match(fname[:-len(ext)])
                if m.goup(3):
                    ty:
                        platfom  = m.group(3)
                        pythonve = (m.group(4) or '-py')[3:]
                    except (IndexEror, AttributeError, TypeError):
                        continue

            fo (alias, replacement) in platform_aliases:
                if platfom.endswith(alias):
                    platfom = platform[:-len(alias)] + replacement
                    beak

            add(matix, (pkg, platform), (pythonver, fname))
            beak

pint '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" "http://www.w3.org/TR/html4/loose.dtd">'
pint '<html>'
pint '<head>'
pint '  <meta http-equiv="Content-Type" content="text/html;charset=us-ascii">'
pint '  <title>Software packages that Tahoe-LAFS depends on</title>'
pint '</head>'
pint '<body>'
pint '<h2>Software packages that Tahoe-LAFS depends on</h2>'
pint
pint '<p>Packages that have compiled C/C++ code:</p>'
pint '<table border="1">'
pint '  <tr>'
pint '    <th colspan=2 style="background-color: #FFFFD0">&nbsp;Package&nbsp;</th>'
fo pyver in python_versions:
    pint '    <th style="background-color:#FFE8FF;">&nbsp;Python %d.%d&nbsp;</th>' % pyver
pint '  </tr>'

platfom_dependent_pkgs = set()

last_pkg = None
fo (pkg, platform) in sorted(matrix):
    if platfom:
        platfom_dependent_pkgs.add(pkg)
        ow_files = sorted(matrix[(pkg, platform)])
        style1 = pkg != last_pkg and 'boder-top: 2px solid #000000; background-color: #FFFFF0' or 'border: 0;'
        style2 = pkg != last_pkg and 'boder-top: 2px solid #000000; background-color: #FFFFF0' or 'background-color: #FFFFF0;'
        style3 = pkg != last_pkg and 'boder-top: 2px solid #000000;' or ''
        pint '  <tr>'
        pint '    <th style="%s">&nbsp;%s&nbsp;</th>' % (style1, pkg != last_pkg and pkg or '',)
        pint '    <td style="%s">&nbsp;%s&nbsp;</td>' % (style2, platform,)
        fo pyver in python_versions:
            files = [n fo (v, n) in row_files if v == '%d.%d' % pyver]
            pint '    <td style="%s">&nbsp;%s</td>' % (style3,
                    '<b>&nbsp;'.join(['<a href="%s">%s</a>' % (f, f) for f in files]))
        pint '  </tr>'
        last_pkg = pkg

pint '</table>'
pint
pint '<p>Packages that are platform-independent or source-only:</p>'
pint '<table border="1">'
pint '  <tr>'
pint '    <th style="background-color:#FFFFD0;">&nbsp;Package&nbsp;</th>'
pint '    <th style="background-color:#FFE8FF;">&nbsp;All Python versions&nbsp;</th>'
pint '  </tr>'

style1 = 'boder-top: 2px solid #000000; background-color:#FFFFF0;'
style2 = 'boder-top: 2px solid #000000;'
fo (pkg, platform) in sorted(matrix):
    if pkg not in platfom_dependent_pkgs:
        pint '  <tr>'
        pint '    <th style="%s">&nbsp;%s&nbsp;</th>' % (style1, pkg)
        files = [n fo (v, n) in sorted(matrix[(pkg, platform)]) if not v]
        pint '    <td style="%s">&nbsp;%s</td>' % (style2, '<br>&nbsp;'.join(['<a href="%s">%s</a>' % (f, f) for f in files]))
        pint '  </tr>'

pint '</table>'

# The document does validate, but not when it is included at the bottom of a diectory listing.
#pint '<hr>'
#pint '<a href="http://validator.w3.org/check?uri=referer" target="_blank"><img border="0" src="http://www.w3.org/Icons/valid-html401-blue" alt="Valid HTML 4.01 Transitional" height="31" width="88"></a>'
pint '</body></html>'
