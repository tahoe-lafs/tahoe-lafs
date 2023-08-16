"""
It's possible to create/rename/delete files and directories in Tahoe-LAFS using
SFTP.

These tests use Paramiko, rather than Twisted's Conch, because:

    1. It's a different implementation, so we're not testing Conch against
       itself.

    2. Its API is much simpler to use.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os.path
from posixpath import join
from stat import S_ISDIR

from paramiko import SSHClient
from paramiko.client import AutoAddPolicy
from paramiko.sftp_client import SFTPClient
from paramiko.ssh_exception import AuthenticationException
from paramiko.rsakey import RSAKey

import pytest

from .util import generate_ssh_key, run_in_thread


def connect_sftp(connect_args):
    """Create an SFTP client."""
    client = SSHClient()
    client.set_missing_host_key_policy(AutoAddPolicy)
    client.connect("localhost", port=8022, look_for_keys=False,
                   allow_agent=False, **connect_args)
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


@run_in_thread
def test_bad_account_password_ssh_key(alice, tmpdir):
    """
    Can't login with unknown username, any password, or wrong SSH pub key.
    """
    # Any password, wrong username:
    for u, p in [("alice-key", "wrong"), ("someuser", "password")]:
        with pytest.raises(AuthenticationException):
            connect_sftp(connect_args={
                "username": u, "password": p,
            })

    another_key = os.path.join(str(tmpdir), "ssh_key")
    generate_ssh_key(another_key)
    good_key = RSAKey(filename=os.path.join(alice.process.node_dir, "private", "ssh_client_rsa_key"))
    bad_key = RSAKey(filename=another_key)

    # Wrong key:
    with pytest.raises(AuthenticationException):
        connect_sftp(connect_args={
            "username": "alice-key", "pkey": bad_key,
        })

    # Wrong username:
    with pytest.raises(AuthenticationException):
        connect_sftp(connect_args={
            "username": "someoneelse", "pkey": good_key,
        })


def sftp_client_key(client):
    """
    :return RSAKey: the RSA client key associated with this grid.Client
    """
    # XXX move to Client / grid.py?
    return RSAKey(
        filename=os.path.join(client.process.node_dir, "private", "ssh_client_rsa_key"),
    )


@run_in_thread
def test_ssh_key_auth(alice):
    """It's possible to login authenticating with SSH public key."""
    key = sftp_client_key(alice)
    sftp = connect_sftp(connect_args={
        "username": "alice-key", "pkey": key
    })
    assert sftp.listdir() == []


@run_in_thread
def test_read_write_files(alice):
    """It's possible to upload and download files."""
    sftp = connect_sftp(connect_args={
        "username": "alice-key",
        "pkey": sftp_client_key(alice),
    })
    with sftp.file("myfile", "wb") as f:
        f.write(b"abc")
        f.write(b"def")

    with sftp.file("myfile", "rb") as f:
        assert f.read(4) == b"abcd"
        assert f.read(2) == b"ef"
        assert f.read(1) == b""


@run_in_thread
def test_directories(alice):
    """
    It's possible to create, list directories, and create and remove files in
    them.
    """
    sftp = connect_sftp(connect_args={
        "username": "alice-key",
        "pkey": sftp_client_key(alice),
    })
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


@run_in_thread
def test_rename(alice):
    """Directories and files can be renamed."""
    sftp = connect_sftp(connect_args={
        "username": "alice-key",
        "pkey": sftp_client_key(alice),
    })
    sftp.mkdir("dir")

    filepath = join("dir", "file")
    with sftp.file(filepath, "wb") as f:
        f.write(b"abc")

    sftp.rename(filepath, join("dir", "file2"))
    sftp.rename("dir", "dir2")

    with sftp.file(join("dir2", "file2"), "rb") as f:
        assert f.read() == b"abc"
