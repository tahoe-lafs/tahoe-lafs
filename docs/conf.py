# -*- coding: utf-8 -*-

extensions = ['recommonmark',
              'sphinx_rtd_theme']
templates_path = ['_templates']
source_suffix = ['.rst', '.md']
master_doc = 'index'
project = u'Next-Gen-Docs-Tahoe-LAFS'
copyright = u'2024, The Tahoe-LAFS Developers'
author = u'The Tahoe-LAFS Developers'

version = u'1.19-dev'
release = u'1.19-dev'

language = "en"
exclude_patterns = ['_build']
pygments_style = 'sphinx'

todo_include_todos = False
html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']
htmlhelp_basename = 'Tahoe-LAFSdoc'
html_extra_path = ['robots.txt']


# -- Options for manual page output ---------------------------------------

# One entry per manual page. List of tuples
# (source start file, name, description, authors, manual section).
man_pages = [
    (master_doc, 'tahoe-lafs', u'Tahoe-LAFS Documentation',
     [author], 1)
]

