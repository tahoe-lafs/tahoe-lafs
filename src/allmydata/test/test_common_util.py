"""
This module has been ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import sys
import random

from hypothesis import given
from hypothesis.strategies import lists, sampled_from
from testtools.matchers import Equals
from twisted.python.reflect import (
    ModuleNotFound,
    namedAny,
)

from .common import (
    SyncTestCase,
    disable_modules,
)
from allmydata.test.common_util import flip_one_bit


class TestFlipOneBit(SyncTestCase):

    def setUp(self):
        super(TestFlipOneBit, self).setUp()
        # I tried using version=1 on PY3 to avoid the if below, to no avail.
        random.seed(42)

    def test_accepts_byte_string(self):
        actual = flip_one_bit(b'foo')
        self.assertEqual(actual, b'fno' if PY2 else b'fom')

    def test_rejects_unicode_string(self):
        self.assertRaises(AssertionError, flip_one_bit, u'foo')



def some_existing_modules():
    """
    Build the names of modules (as native strings) that exist and can be
    imported.
    """
    candidates = sorted(
        name
        for name
        in sys.modules
        if "." not in name
        and sys.modules[name] is not None
    )
    return sampled_from(candidates)

class DisableModulesTests(SyncTestCase):
    """
    Tests for ``disable_modules``.
    """
    def setup_example(self):
        return sys.modules.copy()

    def teardown_example(self, safe_modules):
        sys.modules.update(safe_modules)

    @given(lists(some_existing_modules(), unique=True))
    def test_importerror(self, module_names):
        """
        While the ``disable_modules`` context manager is active any import of the
        modules identified by the names passed to it result in ``ImportError``
        being raised.
        """
        def get_modules():
            return list(
                namedAny(name)
                for name
                in module_names
            )
        before_modules = get_modules()

        with disable_modules(*module_names):
            for name in module_names:
                with self.assertRaises(ModuleNotFound):
                    namedAny(name)

        after_modules = get_modules()
        self.assertThat(before_modules, Equals(after_modules))

    def test_dotted_names_rejected(self):
        """
        If names with "." in them are passed to ``disable_modules`` then
        ``ValueError`` is raised.
        """
        with self.assertRaises(ValueError):
            with disable_modules("foo.bar"):
                pass
