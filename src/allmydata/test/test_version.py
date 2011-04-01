
from twisted.trial import unittest

from allmydata import check_requirement, cross_check, PackagingError
from allmydata.util.verlib import NormalizedVersion as V, \
                                  IrrationalVersionError, \
                                  suggest_normalized_version as suggest


class CheckRequirement(unittest.TestCase):
    def test_check_requirement(self):
        check_requirement("setuptools >= 0.6c6", {"setuptools": ("0.6", "", None)})
        check_requirement("setuptools >= 0.6c6", {"setuptools": ("0.6", "", "distribute")})
        check_requirement("pycrypto == 2.0.1, == 2.1, >= 2.3", {"pycrypto": ("2.1.0", "", None)})
        check_requirement("pycrypto == 2.0.1, == 2.1, >= 2.3", {"pycrypto": ("2.4.0", "", None)})

        check_requirement("zope.interface", {"zope.interface": ("unknown", "", None)})
        check_requirement("mock", {"mock": ("0.6.0", "", None)})
        check_requirement("foo >= 1.0", {"foo": ("1.0", "", None), "bar": ("2.0", "", None)})

        check_requirement("foolscap[secure_connections] >= 0.6.0", {"foolscap": ("0.7.0", "", None)})

        try:
            check_requirement("foolscap[secure_connections] >= 0.6.0", {"foolscap": ("0.6.1+", "", None)})
            # succeeding is ok
        except PackagingError, e:
            self.failUnlessIn("could not parse", str(e))

        self.failUnlessRaises(PackagingError, check_requirement,
                              "foolscap[secure_connections] >= 0.6.0", {"foolscap": ("0.5.1", "", None)})
        self.failUnlessRaises(PackagingError, check_requirement,
                              "pycrypto == 2.0.1, == 2.1, >= 2.3", {"pycrypto": ("2.2.0", "", None)})
        self.failUnlessRaises(PackagingError, check_requirement,
                              "foo >= 1.0", {})
        self.failUnlessRaises(PackagingError, check_requirement,
                              "foo >= 1.0", {"foo": ("irrational", "", None)})

        self.failUnlessRaises(ImportError, check_requirement,
                              "foo >= 1.0", {"foo": (None, None, "foomodule")})

    def test_cross_check_ticket_1355(self):
        # The bug in #1355 is triggered when a version string from either pkg_resources or import
        # is not parseable at all by normalized_version.

        res = cross_check({"foo": ("unparseable", "")}, [("foo", ("1.0", "", None))])
        self.failUnlessEqual(len(res), 1)
        self.failUnlessIn("by pkg_resources could not be parsed", res[0])

        res = cross_check({"foo": ("1.0", "")}, [("foo", ("unparseable", "", None))])
        self.failUnlessEqual(len(res), 1)
        self.failUnlessIn(") could not be parsed", res[0])

    def test_cross_check(self):
        res = cross_check({}, [])
        self.failUnlessEqual(res, [])

        res = cross_check({}, [("sqlite3", ("1.0", "", "blah"))])
        self.failUnlessEqual(res, [])

        res = cross_check({"foo": ("unparseable", "")}, [])
        self.failUnlessEqual(len(res), 1)
        self.failUnlessIn("not found by import", res[0])

        res = cross_check({"argparse": ("unparseable", "")}, [])
        self.failUnlessEqual(len(res), 0)

        res = cross_check({}, [("foo", ("unparseable", "", None))])
        self.failUnlessEqual(len(res), 1)
        self.failUnlessIn("not found by pkg_resources", res[0])

        res = cross_check({"distribute": ("1.0", "/somewhere")}, [("setuptools", ("2.0", "/somewhere", "distribute"))])
        self.failUnlessEqual(len(res), 0)

        res = cross_check({"distribute": ("1.0", "/somewhere")}, [("setuptools", ("2.0", "/somewhere", None))])
        self.failUnlessEqual(len(res), 1)
        self.failUnlessIn("location mismatch", res[0])

        res = cross_check({"distribute": ("1.0", "/somewhere")}, [("setuptools", ("2.0", "/somewhere_different", None))])
        self.failUnlessEqual(len(res), 1)
        self.failUnlessIn("location mismatch", res[0])

        res = cross_check({"zope.interface": ("1.0", "")}, [("zope.interface", ("unknown", "", None))])
        self.failUnlessEqual(len(res), 0)

        res = cross_check({"foo": ("1.0", "")}, [("foo", ("unknown", "", None))])
        self.failUnlessEqual(len(res), 1)
        self.failUnlessIn("could not find a version number", res[0])

        # When pkg_resources and import both find a package, there is only a warning if both
        # the version and the path fail to match.

        res = cross_check({"foo": ("1.0", "/somewhere")}, [("foo", ("2.0", "/somewhere", None))])
        self.failUnlessEqual(len(res), 0)

        res = cross_check({"foo": ("1.0", "/somewhere")}, [("foo", ("1.0", "/somewhere_different", None))])
        self.failUnlessEqual(len(res), 0)

        res = cross_check({"foo": ("1.0-r123", "/somewhere")}, [("foo", ("1.0.post123", "/somewhere_different", None))])
        self.failUnlessEqual(len(res), 0)

        res = cross_check({"foo": ("1.0", "/somewhere")}, [("foo", ("2.0", "/somewhere_different", None))])
        self.failUnlessEqual(len(res), 1)
        self.failUnlessIn("but version '2.0'", res[0])


# based on https://bitbucket.org/tarek/distutilsversion/src/17df9a7d96ef/test_verlib.py

class VersionTestCase(unittest.TestCase):
    versions = ((V('1.0'), '1.0'),
                (V('1.1'), '1.1'),
                (V('1.2.3'), '1.2.3'),
                (V('1.2'), '1.2'),
                (V('1.2.3a4'), '1.2.3a4'),
                (V('1.2c4'), '1.2c4'),
                (V('1.2.3.4'), '1.2.3.4'),
                (V('1.2.3.4.0b3'), '1.2.3.4b3'),
                (V('1.2.0.0.0'), '1.2'),
                (V('1.0.dev345'), '1.0.dev345'),
                (V('1.0.post456.dev623'), '1.0.post456.dev623'))

    def test_basic_versions(self):
        for v, s in self.versions:
            self.failUnlessEqual(str(v), s)

    def test_from_parts(self):
        for v, s in self.versions:
            parts = v.parts
            v2 = V.from_parts(*parts)
            self.failUnlessEqual(v, v2)
            self.failUnlessEqual(str(v), str(v2))

    def test_irrational_versions(self):
        irrational = ('1', '1.2a', '1.2.3b', '1.02', '1.2a03',
                      '1.2a3.04', '1.2.dev.2', '1.2dev', '1.2.dev',
                      '1.2.dev2.post2', '1.2.post2.dev3.post4')

        for s in irrational:
            self.failUnlessRaises(IrrationalVersionError, V, s)

    def test_comparison(self):
        self.failUnlessRaises(TypeError, lambda: V('1.2.0') == '1.2')

        self.failUnlessEqual(V('1.2.0'), V('1.2'))
        self.failIfEqual(V('1.2.0'), V('1.2.3'))
        self.failUnless(V('1.2.0') < V('1.2.3'))
        self.failUnless(V('1.0') > V('1.0b2'))
        self.failUnless(V('1.0') > V('1.0c2') > V('1.0c1') > V('1.0b2') > V('1.0b1')
                        > V('1.0a2') > V('1.0a1'))
        self.failUnless(V('1.0.0') > V('1.0.0c2') > V('1.0.0c1') > V('1.0.0b2') > V('1.0.0b1')
                        > V('1.0.0a2') > V('1.0.0a1'))

        self.failUnless(V('1.0') < V('1.0.post456.dev623'))
        self.failUnless(V('1.0.post456.dev623') < V('1.0.post456')  < V('1.0.post1234'))

        self.failUnless(V('1.0a1')
                        < V('1.0a2.dev456')
                        < V('1.0a2')
                        < V('1.0a2.1.dev456')  # e.g. need to do a quick post release on 1.0a2
                        < V('1.0a2.1')
                        < V('1.0b1.dev456')
                        < V('1.0b2')
                        < V('1.0c1')
                        < V('1.0c2.dev456')
                        < V('1.0c2')
                        < V('1.0.dev7')
                        < V('1.0.dev18')
                        < V('1.0.dev456')
                        < V('1.0.dev1234')
                        < V('1.0')
                        < V('1.0.post456.dev623')  # development version of a post release
                        < V('1.0.post456'))

    def test_suggest_normalized_version(self):
        self.failUnlessEqual(suggest('1.0'), '1.0')
        self.failUnlessEqual(suggest('1.0-alpha1'), '1.0a1')
        self.failUnlessEqual(suggest('1.0c2'), '1.0c2')
        self.failUnlessEqual(suggest('walla walla washington'), None)
        self.failUnlessEqual(suggest('2.4c1'), '2.4c1')

        # from setuptools
        self.failUnlessEqual(suggest('0.4a1.r10'), '0.4a1.post10')
        self.failUnlessEqual(suggest('0.7a1dev-r66608'), '0.7a1.dev66608')
        self.failUnlessEqual(suggest('0.6a9.dev-r41475'), '0.6a9.dev41475')
        self.failUnlessEqual(suggest('2.4preview1'), '2.4c1')
        self.failUnlessEqual(suggest('2.4pre1') , '2.4c1')
        self.failUnlessEqual(suggest('2.1-rc2'), '2.1c2')

        # from pypi
        self.failUnlessEqual(suggest('0.1dev'), '0.1.dev0')
        self.failUnlessEqual(suggest('0.1.dev'), '0.1.dev0')

        # we want to be able to parse Twisted
        # development versions are like post releases in Twisted
        self.failUnlessEqual(suggest('9.0.0+r2363'), '9.0.0.post2363')

        # pre-releases are using markers like "pre1"
        self.failUnlessEqual(suggest('9.0.0pre1'), '9.0.0c1')

        # we want to be able to parse Tcl-TK
        # they use "p1" "p2" for post releases
        self.failUnlessEqual(suggest('1.4p1'), '1.4.post1')

        # from darcsver
        self.failUnlessEqual(suggest('1.8.1-r4956'), '1.8.1.post4956')

        # zetuptoolz
        self.failUnlessEqual(suggest('0.6c16dev3'), '0.6c16.dev3')
