"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import re

unknown_rwcap = u"lafs://from_the_future_rw_\u263A".encode('utf-8')
unknown_rocap = u"ro.lafs://readonly_from_the_future_ro_\u263A".encode('utf-8')
unknown_immcap = u"imm.lafs://immutable_from_the_future_imm_\u263A".encode('utf-8')


def assert_soup_has_favicon(testcase, soup):
    """
    Using a ``TestCase`` object ``testcase``, assert that the passed in
    ``BeautifulSoup`` object ``soup`` contains the tahoe favicon link.
    """
    links = soup.find_all(u'link', rel=u'shortcut icon')
    testcase.assertTrue(
        any(t[u'href'] == u'/icon.png' for t in links), soup)


def assert_soup_has_tag_with_attributes(testcase, soup, tag_name, attrs):
    """
    Using a ``TestCase`` object ``testcase``, assert that the passed
    in ``BeatufulSoup`` object ``soup`` contains a tag ``tag_name``
    (unicode) which has all the attributes in ``attrs`` (dict).
    """
    tags = soup.find_all(tag_name)
    for tag in tags:
        if all(v in tag.attrs.get(k, []) for k, v in attrs.items()):
            # we found every attr in this tag; done
            return tag
    testcase.fail(
        u"No <{}> tags contain attributes: {}".format(tag_name, attrs)
    )


def assert_soup_has_tag_with_attributes_and_content(testcase, soup, tag_name, content, attrs):
    """
    Using a ``TestCase`` object ``testcase``, assert that the passed
    in ``BeatufulSoup`` object ``soup`` contains a tag ``tag_name``
    (unicode) which has all the attributes in ``attrs`` (dict) and
    contains the string ``content`` (unicode).
    """
    assert_soup_has_tag_with_attributes(testcase, soup, tag_name, attrs)
    assert_soup_has_tag_with_content(testcase, soup, tag_name, content)


def _normalized_contents(tag):
    """
    :returns: all the text contents of the tag with whitespace
        normalized: all newlines removed and at most one space between
        words.
    """
    return u" ".join(tag.text.split())


def assert_soup_has_tag_with_content(testcase, soup, tag_name, content):
    """
    Using a ``TestCase`` object ``testcase``, assert that the passed
    in ``BeatufulSoup`` object ``soup`` contains a tag ``tag_name``
    (unicode) which contains the string ``content`` (unicode).
    """
    tags = soup.find_all(tag_name)
    for tag in tags:
        if content in tag.contents:
            return

        # make these "fuzzy" options?
        for c in tag.contents:
            if content in c:
                return

        if content in _normalized_contents(tag):
            return
    testcase.fail(
        u"No <{}> tag contains the text '{}'".format(tag_name, content)
    )


def assert_soup_has_text(testcase, soup, text):
    """
    Using a ``TestCase`` object ``testcase``, assert that the passed in
    ``BeautifulSoup`` object ``soup`` contains the passed in ``text`` anywhere
    as a text node.
    """
    testcase.assertTrue(
        soup.find_all(string=re.compile(re.escape(text))),
        soup)
