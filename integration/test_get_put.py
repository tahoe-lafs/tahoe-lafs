"""
Integration tests for getting and putting files, including reading from stdin
and stdout.
"""

from subprocess import Popen, PIPE

import pytest

from .util import run_in_thread, cli

DATA = b"abc123 this is not utf-8 decodable \xff\x00\x33 \x11"
try:
    DATA.decode("utf-8")
except UnicodeDecodeError:
    pass  # great, what we want
else:
    raise ValueError("BUG, the DATA string was decoded from UTF-8")


@pytest.fixture(scope="session")
def get_put_alias(alice):
    cli(alice, "create-alias", "getput")


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


@run_in_thread
def test_put_from_stdin(alice, get_put_alias, tmpdir):
    """
    It's possible to upload a file via `tahoe put`'s STDIN, and then download
    it to a file.
    """
    tempfile = str(tmpdir.join("file"))
    p = Popen(
        ["tahoe", "--node-directory", alice.node_dir, "put", "-", "getput:fromstdin"],
        stdin=PIPE
    )
    p.stdin.write(DATA)
    p.stdin.close()
    assert p.wait() == 0

    cli(alice, "get", "getput:fromstdin", tempfile)
    assert read_bytes(tempfile) == DATA


def test_get_to_stdout(alice, get_put_alias, tmpdir):
    """
    It's possible to upload a file, and then download it to stdout.
    """
    tempfile = tmpdir.join("file")
    with tempfile.open("wb") as f:
        f.write(DATA)
    cli(alice, "put", str(tempfile), "getput:tostdout")

    p = Popen(
        ["tahoe", "--node-directory", alice.node_dir, "get", "getput:tostdout", "-"],
        stdout=PIPE
    )
    assert p.stdout.read() == DATA
    assert p.wait() == 0
