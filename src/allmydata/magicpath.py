
import re


def path2magic(path):
    return re.sub(ur'[/@]',  lambda m: {u'/': u'@_', u'@': u'@@'}[m.group(0)], path)

def magic2path(path):
    return re.sub(ur'@[_@]', lambda m: {u'@_': u'/', u'@@': u'@'}[m.group(0)], path)
