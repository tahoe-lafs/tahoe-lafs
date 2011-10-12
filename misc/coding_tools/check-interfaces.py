
# To check a particular Tahoe source distribution, this should be invoked from
# the root directory of that distribution as
#
#   bin/tahoe @misc/coding_tools/check-interfaces.py

import os, sys, re

import zope.interface as zi
# We use the forked version of verifyClass below.
#from zope.interface.verify import verifyClass
from zope.interface.advice import addClassAdvisor


interesting_modules = re.compile(r'(allmydata)|(foolscap)\..*')
excluded_classnames = re.compile(r'(_)|(Mock)|(Fake)|(Dummy).*')
excluded_file_basenames = re.compile(r'(check)|(bench)_.*')


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
                        print >>sys.stderr, ("%s.%s does not correctly implement %s.%s:\n%s"
                                             % (cls.__module__, cls.__name__,
                                                interface.__module__, interface.__name__, e))
        else:
            other_modules_with_violations.add(cls.__module__)
        return cls

    f_locals['__implements_advice_data__'] = interfaces, zi.classImplements
    addClassAdvisor(_implements_advice, depth=2)


def check():
    # patchee-monkey
    zi.implements = strictly_implements

    # attempt to avoid side-effects from importing command scripts
    sys.argv = ['', '--help']

    # import modules under src/
    srcdir = 'src'
    for (dirpath, dirnames, filenames) in os.walk(srcdir):
        for fn in filenames:
            (basename, ext) = os.path.splitext(fn)
            if ext in ('.pyc', '.pyo') and not os.path.exists(os.path.join(dirpath, basename+'.py')):
                print >>sys.stderr, ("Warning: no .py source file for %r.\n"
                                     % (os.path.join(dirpath, fn),))

            if ext == '.py' and not excluded_file_basenames.match(basename):
                relpath = os.path.join(dirpath[len(srcdir)+1:], basename)
                module = relpath.replace(os.sep, '/').replace('/', '.')
                try:
                    __import__(module)
                except ImportError:
                    import traceback
                    traceback.print_exc()
                    print >>sys.stderr

    others = list(other_modules_with_violations)
    others.sort()
    print >>sys.stderr, "There were also interface violations in:\n", ", ".join(others), "\n"


# Forked from
# http://svn.zope.org/*checkout*/Zope3/trunk/src/zope/interface/verify.py?content-type=text%2Fplain&rev=27687
# but modified to report all interface violations rather than just the first.

##############################################################################
#
# Copyright (c) 2001, 2002 Zope Corporation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
"""Verify interface implementations

$Id$
"""
from zope.interface.exceptions import BrokenImplementation, DoesNotImplement
from zope.interface.exceptions import BrokenMethodImplementation
from types import FunctionType, MethodType
from zope.interface.interface import fromMethod, fromFunction, Method

# This will be monkey-patched when running under Zope 2, so leave this
# here:
MethodTypes = (MethodType, )


def _verify(iface, candidate, tentative=0, vtype=None):
    """Verify that 'candidate' might correctly implements 'iface'.

    This involves:

      o Making sure the candidate defines all the necessary methods

      o Making sure the methods have the correct signature

      o Making sure the candidate asserts that it implements the interface

    Note that this isn't the same as verifying that the class does
    implement the interface.

    If optional tentative is true, suppress the "is implemented by" test.
    """

    if vtype == 'c':
        tester = iface.implementedBy
    else:
        tester = iface.providedBy

    violations = []
    def format(e):
        return "    " + str(e).strip() + "\n"

    if not tentative and not tester(candidate):
        violations.append(format(DoesNotImplement(iface)))

    # Here the `desc` is either an `Attribute` or `Method` instance
    for name, desc in iface.namesAndDescriptions(1):
        if not hasattr(candidate, name):
            if (not isinstance(desc, Method)) and vtype == 'c':
                # We can't verify non-methods on classes, since the
                # class may provide attrs in it's __init__.
                continue

            if isinstance(desc, Method):
                violations.append("    The %r method was not provided.\n" % (name,))
            else:
                violations.append("    The %r attribute was not provided.\n" % (name,))
            continue

        attr = getattr(candidate, name)
        if not isinstance(desc, Method):
            # If it's not a method, there's nothing else we can test
            continue

        if isinstance(attr, FunctionType):
            # should never get here, since classes should not provide functions
            meth = fromFunction(attr, iface, name=name)
        elif (isinstance(attr, MethodTypes)
              and type(attr.im_func) is FunctionType):
            meth = fromMethod(attr, iface, name)
        else:
            if not callable(attr):
                violations.append(format(BrokenMethodImplementation(name, "Not a method")))
            # sigh, it's callable, but we don't know how to intrspect it, so
            # we have to give it a pass.
            continue

        # Make sure that the required and implemented method signatures are
        # the same.
        desc = desc.getSignatureInfo()
        meth = meth.getSignatureInfo()

        mess = _incompat(desc, meth)
        if mess:
            violations.append(format(BrokenMethodImplementation(name, mess)))

    if violations:
        raise Exception("".join(violations))
    return True

def verifyClass(iface, candidate, tentative=0):
    return _verify(iface, candidate, tentative, vtype='c')

def verifyObject(iface, candidate, tentative=0):
    return _verify(iface, candidate, tentative, vtype='o')

def _incompat(required, implemented):
    #if (required['positional'] !=
    #    implemented['positional'][:len(required['positional'])]
    #    and implemented['kwargs'] is None):
    #    return 'imlementation has different argument names'
    if len(implemented['required']) > len(required['required']):
        return 'implementation requires too many arguments'
    if ((len(implemented['positional']) < len(required['positional']))
        and not implemented['varargs']):
        return "implementation doesn't allow enough arguments"
    if required['kwargs'] and not implemented['kwargs']:
        return "implementation doesn't support keyword arguments"
    if required['varargs'] and not implemented['varargs']:
        return "implementation doesn't support variable arguments"


check()
