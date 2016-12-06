from twisted.trial import unittest
from allmydata.mutable.publish import MutableData

class DataHandle(unittest.TestCase):
    def setUp(self):
        self.test_data = "Test Data" * 50000
        self.uploadable = MutableData(self.test_data)


    def test_datahandle_read(self):
        chunk_size = 10
        for i in xrange(0, len(self.test_data), chunk_size):
            data = self.uploadable.read(chunk_size)
            data = "".join(data)
            start = i
            end = i + chunk_size
            self.failUnlessEqual(data, self.test_data[start:end])


    def test_datahandle_get_size(self):
        actual_size = len(self.test_data)
        size = self.uploadable.get_size()
        self.failUnlessEqual(size, actual_size)


    def test_datahandle_get_size_out_of_order(self):
        # We should be able to call get_size whenever we want without
        # disturbing the location of the seek pointer.
        chunk_size = 100
        data = self.uploadable.read(chunk_size)
        self.failUnlessEqual("".join(data), self.test_data[:chunk_size])

        # Now get the size.
        size = self.uploadable.get_size()
        self.failUnlessEqual(size, len(self.test_data))

        # Now get more data. We should be right where we left off.
        more_data = self.uploadable.read(chunk_size)
        start = chunk_size
        end = chunk_size * 2
        self.failUnlessEqual("".join(more_data), self.test_data[start:end])
