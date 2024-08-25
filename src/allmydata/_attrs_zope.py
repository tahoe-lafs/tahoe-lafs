import attrs

@attrs.define(repr=False)
class _ProvidesValidator:
    interface = attrs.field()

    def __call__(self, inst, attr, value):
        """
        We use a callable class to be able to change the ``__repr__``.
        """
        if not self.interface.providedBy(value):
            msg = f"'{attr.name}' must provide {self.interface!r} which {value!r} doesn't."
            raise TypeError(
                msg,
                attr,
                self.interface,
                value,
            )

    def __repr__(self):
        return f"<provides validator for interface {self.interface!r}>"


def provides(interface):
    """
    A validator that raises a `TypeError` if the initializer is called
    with an object that does not provide the requested *interface* (checks are
    performed using ``interface.providedBy(value)`` (see `zope.interface
    <https://zopeinterface.readthedocs.io/en/latest/>`_).

    :param interface: The interface to check for.
    :type interface: ``zope.interface.Interface``

    :raises TypeError: With a human readable error message, the attribute
        (of type `attrs.Attribute`), the expected interface, and the
        value it got.
    """
    return _ProvidesValidator(interface)
