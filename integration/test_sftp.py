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

from paramiko import SSHClient
from paramiko.client import AutoAddPolicy
from paramiko.sftp_client import SFTPClient


def test_read_write_files(alice):
    """It's possible to upload and download files."""
    client = SSHClient()
    client.set_missing_host_key_policy(AutoAddPolicy)
    client.connect(
        "localhost", username="alice", password="password", port=8022,
        look_for_keys=False
    )
    sftp = SFTPClient.from_transport(client.get_transport())
    f = sftp.file("myfile", "wb")
    f.write(b"abc")
    f.write(b"def")
    f.close()
    f = sftp.file("myfile", "rb")
    assert f.read(4) == b"abcd"
    assert f.read(2) == b"ef"
    assert f.read(1) == b""
    f.close()
