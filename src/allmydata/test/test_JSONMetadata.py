import mock

from twisted.trial.unittest import TestCase

from allmydata.web.common import get_filenode_metadata, SDMF_VERSION, MDMF_VERSION
from allmydata.immutable.filenode import ImmutableFileNode

class CommonFixture(object):
    def setUp(self):
        self.mockfilenode = mock.Mock()

    def test_size_is_0(self):
        """If get_size doesn't return None the returned metadata must contain "size"."""
        self.mockfilenode.get_size.return_value = 0
        metadata = get_filenode_metadata(self.mockfilenode)
        self.failUnlessIn('size', metadata)

    def test_size_is_1000(self):
        """If get_size doesn't return None the returned metadata must contain "size"."""
        self.mockfilenode.get_size.return_value = 1000
        metadata = get_filenode_metadata(self.mockfilenode)
        self.failUnlessIn('size', metadata)

    def test_size_is_None(self):
        """If get_size returns None the returned metadata must not contain "size"."""
        self.mockfilenode.get_size.return_value = None
        metadata = get_filenode_metadata(self.mockfilenode)
        self.failIfIn('size', metadata)


class Test_GetFileNodeMetaData_Immutable(CommonFixture, TestCase):
    def setUp(self):
        CommonFixture.setUp(self)
        self.mockfilenode.is_mutable.return_value = False


class Test_GetFileNodeMetaData_SDMF(CommonFixture, TestCase):
    def setUp(self):
        CommonFixture.setUp(self)
        self.mockfilenode.is_mutable.return_value = True
        self.mockfilenode.get_version.return_value = SDMF_VERSION


class Test_GetFileNodeMetaData_MDMF(CommonFixture, TestCase):
    def setUp(self):
        CommonFixture.setUp(self)
        self.mockfilenode.is_mutable.return_value = True
        self.mockfilenode.get_version.return_value = MDMF_VERSION
