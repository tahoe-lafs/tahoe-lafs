import mock

from twisted.trial.unittest import TestCase

from allmydata.web.common import get_filenode_metadata, SDMF_VERSION, MDMF_VERSION
from allmydata.immutable.filenode import ImmutableFileNode

class CommonFixture(TestCase):
    def setUp(self):
        self.mockfilenode = mock.Mock()


class Test_GetFileNodeMetaData_Immutable(CommonFixture):
    def setUp(self):
        CommonFixture.setUp(self)
        self.mockfilenode.is_mutable.return_value = False

    def test_size_not_None(self):
        """If get_size doesn't return None the returned metadata must contain "size"."""
        self.mockfilenode.get_size.return_value = 100
        metadata = get_filenode_metadata(self.mockfilenode)
        self.failUnlessIn('size', metadata.keys())

    def test_size_is_None(self):
        """If get_size returns None the returned metadata must not contain "size"."""
        self.mockfilenode.get_size.return_value = None
        metadata = get_filenode_metadata(self.mockfilenode)
        self.failIfIn('size', metadata.keys())


class Test_GetFileNodeMetaData_SDMF(CommonFixture):
    def setUp(self):
        CommonFixture.setUp(self)
        self.mockfilenode.is_mutable.return_value = True
        self.mockfilenode.get_version.return_value = SDMF_VERSION

    def test_size_not_None(self):
        """If get_size doesn't return None the returned metadata must contain "size"."""
        self.mockfilenode.get_size.return_value = 100
        metadata = get_filenode_metadata(self.mockfilenode)
        self.failUnlessIn('size', metadata.keys())

    def test_size_is_None(self):
        """If get_size returns None the returned metadata must not contain "size"."""
        self.mockfilenode.get_size.return_value = None
        metadata = get_filenode_metadata(self.mockfilenode)
        self.failIfIn('size', metadata.keys())


class Test_GetFileNodeMetaData_MDMF(CommonFixture):
    def setUp(self):
        CommonFixture.setUp(self)
        self.mockfilenode.is_mutable.return_value = True
        self.mockfilenode.get_version.return_value = MDMF_VERSION

    def test_size_not_None(self):
        """If get_size doesn't return None the returned metadata must contain "size"."""
        self.mockfilenode.get_size.return_value = 100
        metadata = get_filenode_metadata(self.mockfilenode)
        self.failUnlessIn('size', metadata.keys())

    def test_size_is_None(self):
        """If get_size returns None the returned metadata must not contain "size"."""
        self.mockfilenode.get_size.return_value = None
        metadata = get_filenode_metadata(self.mockfilenode)
        self.failIfIn('size', metadata.keys())
