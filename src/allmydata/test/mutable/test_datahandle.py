"""
Ported to Python 3.
"""

from ..common import SyncTestCase
from allmydata.mutable.publish import MutableData
from testtools.matchers import Equals, HasLength


class DataHandle(SyncTestCase):
    def setUp(self):
        super(DataHandle, self).setUp()
        self.test_data = b"Test Data" * 50000
        self.uploadable = MutableData(self.test_data)


    def test_datahandle_read(self):
        chunk_size = 10
        for i in range(0, len(self.test_data), chunk_size):
            data = self.uploadable.read(chunk_size)
            data = b"".join(data)
            start = i
            end = i + chunk_size
            self.assertThat(data, Equals(self.test_data[start:end]))


    def test_datahandle_get_size(self):
        actual_size = len(self.test_data)
        size = self.uploadable.get_size()
        self.assertThat(size, Equals(actual_size))


    def test_datahandle_get_size_out_of_order(self):
        # We should be able to call get_size whenever we want without
        # disturbing the location of the seek pointer.
        chunk_size = 100
        data = self.uploadable.read(chunk_size)
        self.assertThat(b"".join(data), Equals(self.test_data[:chunk_size]))

        # Now get the size.
        size = self.uploadable.get_size()
        self.assertThat(self.test_data, HasLength(size))

        # Now get more data. We should be right where we left off.
        more_data = self.uploadable.read(chunk_size)
        start = chunk_size
        end = chunk_size * 2
        self.assertThat(b"".join(more_data), Equals(self.test_data[start:end]))
