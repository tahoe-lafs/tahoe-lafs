
import urllib2
from urllib import urlencode

from allmydata.util.assertutil import precondition
from allmydata import uri

class AuthError(Exception):
    pass

def unicode_to_utf8(uobj):
    assert precondition(isinstance(uobj, unicode))
    return uobj.encode('utf-8')

def post(url, args):
    argstr = urlencode(args)
    conn = urllib2.urlopen(url, argstr)
    return conn.read()

def get_root_cap(url, user, passwd):
    args = {
        'action': 'authenticate',
        'email': unicode_to_utf8(user),
        'passwd': unicode_to_utf8(passwd),
        }
    root_cap = post(url, args)
    if root_cap == '0':
        raise AuthError()
    elif not uri.is_uri(root_cap):
        raise ValueError('%r is not a URI' % (root_cap,))
    else:
        return root_cap

def get_introducer_furl(url):
    return post(url, { 'action': 'getintroducerfurl' })

def main():

    URL = 'https://www-test.allmydata.com/native_client2.php'

    print get_introducer_furl(URL)
    print get_root_cap(URL, u'user', u'pass')


if __name__ == '__main__':
    main()
