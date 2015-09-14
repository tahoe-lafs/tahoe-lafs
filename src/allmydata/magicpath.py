
import re
import os.path

def path2magic(path):
    return re.sub(ur'[/@]',  lambda m: {u'/': u'@_', u'@': u'@@'}[m.group(0)], path)

def magic2path(path):
    return re.sub(ur'@[_@]', lambda m: {u'@_': u'/', u'@@': u'@'}[m.group(0)], path)


IGNORE_SUFFIXES = [u'.backup', u'.tmp', u'.conflicted']
IGNORE_PREFIXES = [u'.']

def should_ignore_file(path_u):
    for suffix in IGNORE_SUFFIXES:
        if path_u.endswith(suffix):
            return True
    while path_u != u"":
        path_u, tail_u = os.path.split(path_u)
        if tail_u.startswith(u"."):
            return True
    return False
