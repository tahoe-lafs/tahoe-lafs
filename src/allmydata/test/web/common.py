
import re

unknown_rwcap = u"lafs://from_the_future_rw_\u263A".encode('utf-8')
unknown_rocap = u"ro.lafs://readonly_from_the_future_ro_\u263A".encode('utf-8')
unknown_immcap = u"imm.lafs://immutable_from_the_future_imm_\u263A".encode('utf-8')

FAVICON_MARKUP = '<link href="/icon.png" rel="shortcut icon" />'


def assert_soup_has_favicon(testcase, soup):
    """
    Using a ``TestCase`` object ``testcase``, assert that the passed in
    ``BeautifulSoup`` object ``soup`` contains the tahoe favicon link.
    """
    links = soup.find_all(u'link', rel=u'shortcut icon')
    testcase.assert_(
        any(t[u'href'] == u'/icon.png' for t in links), soup)


def assert_soup_has_text(testcase, soup, text):
    """
    Using a ``TestCase`` object ``testcase``, assert that the passed in
    ``BeautifulSoup`` object ``soup`` contains the passed in ``text`` anywhere
    as a text node.
    """
    testcase.assert_(
        soup.find_all(string=re.compile(re.escape(text))),
        soup)
