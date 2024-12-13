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


Organizing Tahoe-LAFS documentation
-----------------------------------

Tahoe-LAFS documentation has been a mishmash of many things that are
useful to many people, with little organization, and, as a result,
confusing and hard-to-approach.  We are working on improving this.

It is reasonable to expect that documentation files in "docs"
directory will serve different and possibly overlapping groups of
readers, so the top-level sections are organized based on the likely
needs of those almost-distinct groups.  We have:

  (a) New and experienced users of Tahoe-LAFS, who mainly need an
      operating manual to the software.  Notes under the section
      titled "Getting Started with Tahoe-LAFS" will be the most useful
      to them.

  (b) Project contributors, both new and experienced.  This group
      includes developers, issue reporters, and documentation writers.
      It will help this group to have the project's processes and
      guidelines written down.  The section titled "Contributing to
      Tahoe-LAFS" is meant to be useful for this group.

  (c) Those who want to know various implementation details about the
      project.  This group might include people who are mainly curious
      and those who want change things.  We could expect an overlap
      between members of group (a) who want to know more and members
      of group (b).  The sections titled "Tahoe-LAFS in Depth" and
      "Specifications" could be of interest to them.

  (d) There's also the broader community.  This includes people with a
      general interest in Tahoe-LAFS project, and people from groups
      both (a) and (b).  They will find "Notes of Community Interest"
      useful.

When you add new content or remove old content to Tahoe-LAFS docs, it
would be helpful to organize your changes with the above-stated groups
of readers in mind.

This directory also contains old notes that are mainly of historical
interest, under the section titled "Notes of Historical Interest".
Those could be removed someday, after sufficient consideration.
