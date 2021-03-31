If you are reading Tahoe-LAFS documentation
-------------------------------------------

If you are reading Tahoe-LAFS documentation at a code hosting site or
from a checked-out source tree, the preferred place to view the docs
is http://tahoe-lafs.readthedocs.io/en/latest/. Code-hosting sites do
not render cross-document links or images correctly.


If you are writing Tahoe-LAFS documentation
-------------------------------------------

To edit Tahoe-LAFS docs, you will need a checked-out source tree. You
can edit the `.rst` files in this directory using a text editor, and
then generate HTML output using Sphinx, a program that can produce its
output in HTML and other formats.

Files with `.rst` extension use reStructuredText markup format, which
is the format Sphinx natively handles. To learn more about Sphinx, and
for a friendly primer on reStructuredText, please see Sphinx project's
documentation, available at:

https://www.sphinx-doc.org/

If you have `tox` installed, you can run `tox -e docs` and then open
the resulting docs/_build/html/index.html in your web browser.

Note that Sphinx can also process Python docstrings to generate API
documentation. Tahoe-LAFS currently does not use Sphinx for this
purpose.
