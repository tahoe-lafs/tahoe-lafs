"""
It's possible to create/rename/delete files and directories in Tahoe-LAFS using
SFTP.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from posixpath import join
from stat import S_ISDIR

from paramiko import SSHClient
from paramiko.client import AutoAddPolicy
from paramiko.sftp_client import SFTPClient
from paramiko.ssh_exception import AuthenticationException

import pytest


def connect_sftp(alice, username="alice", password="password"):
    """Create an SFTP client."""
    client = SSHClient()
    client.set_missing_host_key_policy(AutoAddPolicy)
    client.connect(
        "localhost", username=username, password=password, port=8022,
        look_for_keys=False
    )
    sftp = SFTPClient.from_transport(client.get_transport())

    def rmdir(path, delete_root=True):
        for f in sftp.listdir_attr(path=path):
            childpath = join(path, f.filename)
            if S_ISDIR(f.st_mode):
                rmdir(childpath)
            else:
                sftp.remove(childpath)
        if delete_root:
            sftp.rmdir(path)

    # Delete any files left over from previous tests :(
    rmdir("/", delete_root=False)

    return sftp


def test_bad_account_password(alice):
    """Can't login with unknown username or wrong password."""
    for u, p in [("alice", "wrong"), ("someuser", "password")]:
        with pytest.raises(AuthenticationException):
            connect_sftp(alice, u, p)


def test_read_write_files(alice):
    """It's possible to upload and download files."""
    sftp = connect_sftp(alice)
    f = sftp.file("myfile", "wb")
    f.write(b"abc")
    f.write(b"def")
    f.close()
    f = sftp.file("myfile", "rb")
    assert f.read(4) == b"abcd"
    assert f.read(2) == b"ef"
    assert f.read(1) == b""
    f.close()


def test_directories(alice):
    """
    It's possible to create, list directories, and create and remove files in
    them.
    """
    sftp = connect_sftp(alice)
    assert sftp.listdir() == []

    sftp.mkdir("childdir")
    assert sftp.listdir() == ["childdir"]

    with sftp.file("myfile", "wb") as f:
        f.write(b"abc")
    assert sorted(sftp.listdir()) == ["childdir", "myfile"]

    sftp.chdir("childdir")
    assert sftp.listdir() == []

    with sftp.file("myfile2", "wb") as f:
        f.write(b"def")
    assert sftp.listdir() == ["myfile2"]

    sftp.chdir(None)  # root
    with sftp.file("childdir/myfile2", "rb") as f:
        assert f.read() == b"def"

    sftp.remove("myfile")
    assert sftp.listdir() == ["childdir"]

    sftp.rmdir("childdir")
    assert sftp.listdir() == []
