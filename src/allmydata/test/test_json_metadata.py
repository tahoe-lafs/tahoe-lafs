
from twisted.trial.unittest import TestCase

from allmydata.web.common import get_filenode_metadata, SDMF_VERSION, MDMF_VERSION


class MockFileNode(object):
    def __init__(self, size, mutable_version=None):
        self.size = size
        self.mutable_version = mutable_version

    def get_size(self):
        return self.size

    def is_mutable(self):
        return self.mutable_version is not None

    def get_version(self):
        if self.mutable_version is None:
            raise AttributeError()
        return self.mutable_version


class CommonFixture(object):
    def test_size_is_0(self):
        """If get_size doesn't return None the returned metadata must contain "size"."""
        mockfilenode = MockFileNode(0, mutable_version=self.mutable_version)
        metadata = get_filenode_metadata(mockfilenode)
        self.failUnlessEqual(metadata['size'], 0)

    def test_size_is_1000(self):
        """1000 is sufficiently large to guarantee the cap is not a literal."""
        mockfilenode = MockFileNode(1000, mutable_version=self.mutable_version)
        metadata = get_filenode_metadata(mockfilenode)
        self.failUnlessEqual(metadata['size'], 1000)

    def test_size_is_None(self):
        """If get_size returns None the returned metadata must not contain "size"."""
        mockfilenode = MockFileNode(None, mutable_version=self.mutable_version)
        metadata = get_filenode_metadata(mockfilenode)
        self.failIfIn('size', metadata)


class Test_GetFileNodeMetaData_Immutable(CommonFixture, TestCase):
    def setUp(self):
        self.mutable_version = None


class Test_GetFileNodeMetaData_SDMF(CommonFixture, TestCase):
    def setUp(self):
        self.mutable_version = SDMF_VERSION


class Test_GetFileNodeMetaData_MDMF(CommonFixture, TestCase):
    def setUp(self):
        self.mutable_version = MDMF_VERSION
