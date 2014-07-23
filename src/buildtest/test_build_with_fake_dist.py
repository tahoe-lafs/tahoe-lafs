#!/usr/bin/env python
# -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-
# -*- indent-tabs-mode: nil -*-

from twisted.trial import unittest

class T(unittest.TestCase):
    def test_version(self):
        import fakedependency
        self.failUnlessEqual(fakedependency.__version__, '1.0.0')
