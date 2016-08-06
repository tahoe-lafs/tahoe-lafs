import os
from cStringIO import StringIO
from twisted.trial import unittest
from allmydata.mutable.publish import MutableFileHandle

class FileHandle(unittest.TestCase):
    def setUp(self):
        self.test_data = "Test Data" * 50000
        self.sio = StringIO(self.test_data)
        self.uploadable = MutableFileHandle(self.sio)


    def test_filehandle_read(self):
        self.basedir = "mutable/FileHandle/test_filehandle_read"
        chunk_size = 10
        for i in xrange(0, len(self.test_data), chunk_size):
            data = self.uploadable.read(chunk_size)
            data = "".join(data)
            start = i
            end = i + chunk_size
            self.failUnlessEqual(data, self.test_data[start:end])


    def test_filehandle_get_size(self):
        self.basedir = "mutable/FileHandle/test_filehandle_get_size"
        actual_size = len(self.test_data)
        size = self.uploadable.get_size()
        self.failUnlessEqual(size, actual_size)


    def test_filehandle_get_size_out_of_order(self):
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


    def test_filehandle_file(self):
        # Make sure that the MutableFileHandle works on a file as well
        # as a StringIO object, since in some cases it will be asked to
        # deal with files.
        self.basedir = self.mktemp()
        # necessary? What am I doing wrong here?
        os.mkdir(self.basedir)
        f_path = os.path.join(self.basedir, "test_file")
        f = open(f_path, "w")
        f.write(self.test_data)
        f.close()
        f = open(f_path, "r")

        uploadable = MutableFileHandle(f)

        data = uploadable.read(len(self.test_data))
        self.failUnlessEqual("".join(data), self.test_data)
        size = uploadable.get_size()
        self.failUnlessEqual(size, len(self.test_data))


    def test_close(self):
        # Make sure that the MutableFileHandle closes its handle when
        # told to do so.
        self.uploadable.close()
        self.failUnless(self.sio.closed)
