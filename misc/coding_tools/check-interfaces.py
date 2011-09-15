
# To check a particular Tahoe source distribution, this should be invoked from
# the root directory of that distribution as
#
#   bin/tahoe @misc/coding_tools/check-interfaces.py

import os, sys, re

import zope.interface as zi
from zope.interface.verify import verifyClass
from zope.interface.advice import addClassAdvisor


interesting_modules = re.compile(r'(allmydata)|(foolscap)\..*')
excluded_classnames = re.compile(r'(_)|(Mock)|(Fake).*')
excluded_file_basenames = re.compile(r'check_.*')


other_modules_with_violations = set()

# deep magic
def strictly_implements(*interfaces):
    frame = sys._getframe(1)
    f_locals = frame.f_locals

    # Try to make sure we were called from a class def. Assumes Python > 2.2.
    if f_locals is frame.f_globals or '__module__' not in f_locals:
        raise TypeError("implements can be used only from a class definition.")

    if '__implements_advice_data__' in f_locals:
        raise TypeError("implements can be used only once in a class definition.")

    def _implements_advice(cls):
        interfaces, classImplements = cls.__dict__['__implements_advice_data__']
        del cls.__implements_advice_data__
        classImplements(cls, *interfaces)

        if interesting_modules.match(cls.__module__):
            if not excluded_classnames.match(cls.__name__):
                for interface in interfaces:
                    try:
                        verifyClass(interface, cls)
                    except Exception, e:
                        print >>sys.stderr, ("%s.%s does not implement %s.%s:\n%s"
                                             % (cls.__module__, cls.__name__,
                                                interface.__module__, interface.__name__, e))
        else:
            other_modules_with_violations.add(cls.__module__)
        return cls

    f_locals['__implements_advice_data__'] = interfaces, zi.classImplements
    addClassAdvisor(_implements_advice, depth=2)


# patchee-monkey
zi.implements = strictly_implements


# attempt to avoid side-effects from importing command scripts
sys.argv = ['', '--help']


from twisted.python.filepath import FilePath

# import modules under src/
src = FilePath('src')
for fp in src.walk():
    (basepath, ext) = fp.splitext()
    if ext == '.py' and not excluded_file_basenames.match(fp.basename()):
        relpath = os.path.relpath(basepath, src.path)
        module = relpath.replace(os.path.sep, '/').replace('/', '.')
        try:
            __import__(module)
        except ImportError:
            import traceback
            traceback.print_exc()
            print >>sys.stderr

others = list(other_modules_with_violations)
others.sort()
print >>sys.stderr, "There were also interface violations in:\n", ", ".join(others), "\n"
