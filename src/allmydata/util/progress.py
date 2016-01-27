"""
Utilities relating to computing progress information.

Ties in with the "consumer" module also
"""

from allmydata.interfaces import IProgress
from zope.interface import implementer


@implementer(IProgress)
class PercentProgress(object):
    """
    Represents progress as a percentage, from 0.0 to 100.0
    """

    def __init__(self, total_size=None):
        self._value = 0.0
        self.set_progress_total(total_size)

    def set_progress(self, value):
        "IProgress API"
        self._value = value

    def set_progress_total(self, size):
        "IProgress API"
        if size is not None:
            size = float(size)
        self._total_size = size

    @property
    def progress(self):
        if self._total_size is None:
            return 0  # or 1.0?
        if self._total_size <= 0.0:
            return 0
        return (self._value / self._total_size) * 100.0
