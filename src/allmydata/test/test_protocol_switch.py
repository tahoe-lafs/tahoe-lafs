"""
Unit tests for ``allmydata.protocol_switch``.

By its nature, most of the testing needs to be end-to-end; essentially any test
that uses real Foolscap (``test_system.py``, integration tests) ensures
Foolscap still works.  ``test_istorageserver.py`` tests the HTTP support.
"""

from foolscap.negotiate import Negotiation

from .common import TestCase
from ..protocol_switch import _PretendToBeNegotiation


class UtilityTests(TestCase):
    """Tests for utilities in the protocol switch code."""

    def test_metaclass(self):
        """
        A class that has the ``_PretendToBeNegotiation`` metaclass will support
        ``isinstance()``'s normal semantics on its own instances, but will also
        indicate that ``Negotiation`` instances are its instances.
        """

        class Parent(metaclass=_PretendToBeNegotiation):
            pass

        class Child(Parent):
            pass

        class Other:
            pass

        p = Parent()
        self.assertIsInstance(p, Parent)
        self.assertIsInstance(Negotiation(), Parent)
        self.assertNotIsInstance(Other(), Parent)

        c = Child()
        self.assertIsInstance(c, Child)
        self.assertIsInstance(c, Parent)
        self.assertIsInstance(Negotiation(), Child)
        self.assertNotIsInstance(Other(), Child)
