
import re

unknown_rwcap = u"lafs://from_the_future_rw_\u263A".encode('utf-8')
unknown_rocap = u"ro.lafs://readonly_from_the_future_ro_\u263A".encode('utf-8')
unknown_immcap = u"imm.lafs://immutable_from_the_future_imm_\u263A".encode('utf-8')

FAVICON_MARKUP = '<link href="/icon.png" rel="shortcut icon" />'

def assert_soup_has_favicon(testcase, soup):
    links = soup.find_all('link', rel='shortcut icon')
    testcase.assert_(
        any(t['href'] == '/icon.png' for t in links), soup)

def assert_soup_has_text(testcase, soup, text):
    testcase.assert_(
        soup.find_all(string=re.compile(re.escape(text))),
        soup)
