
import re
import os.path

def path2magic(path):
    return re.sub(ur'[/@]',  lambda m: {u'/': u'@_', u'@': u'@@'}[m.group(0)], path)

def magic2path(path):
    return re.sub(ur'@[_@]', lambda m: {u'@_': u'/', u'@@': u'@'}[m.group(0)], path)


IGNORE_SUFFIXES = ['.backup', '.tmp', '.conflicted']
IGNORE_PREFIXES = ['.']

def should_ignore_file(path_u):
    for suffix in IGNORE_SUFFIXES:
        if path_u.endswith(suffix):
            return True
    while True:
        head, tail = os.path.split(path_u)
        if tail != "":
            for prefix in IGNORE_PREFIXES:
                if tail.startswith(prefix):
                    return True
                else:
                    path_u = head
        else:
            if head == "":
                return False
    return False
