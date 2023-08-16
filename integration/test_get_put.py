"""
Integration tests for getting and putting files, including reading from stdin
and stdout.
"""

from subprocess import Popen, PIPE, check_output, check_call

import pytest
from twisted.internet import reactor
from twisted.internet.threads import blockingCallFromThread

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
    cli(alice.process, "create-alias", "getput")


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
        ["tahoe", "--node-directory", alice.process.node_dir, "put", "-", "getput:fromstdin"],
        stdin=PIPE
    )
    p.stdin.write(DATA)
    p.stdin.close()
    assert p.wait() == 0

    cli(alice.process, "get", "getput:fromstdin", tempfile)
    assert read_bytes(tempfile) == DATA


@run_in_thread
def test_get_to_stdout(alice, get_put_alias, tmpdir):
    """
    It's possible to upload a file, and then download it to stdout.
    """
    tempfile = tmpdir.join("file")
    with tempfile.open("wb") as f:
        f.write(DATA)
    cli(alice.process, "put", str(tempfile), "getput:tostdout")

    p = Popen(
        ["tahoe", "--node-directory", alice.process.node_dir, "get", "getput:tostdout", "-"],
        stdout=PIPE
    )
    assert p.stdout.read() == DATA
    assert p.wait() == 0


@run_in_thread
def test_large_file(alice, get_put_alias, tmp_path):
    """
    It's possible to upload and download a larger file.

    We avoid stdin/stdout since that's flaky on Windows.
    """
    tempfile = tmp_path / "file"
    with tempfile.open("wb") as f:
        f.write(DATA * 1_000_000)
    cli(alice.process, "put", str(tempfile), "getput:largefile")

    outfile = tmp_path / "out"
    check_call(
        ["tahoe", "--node-directory", alice.process.node_dir, "get", "getput:largefile", str(outfile)],
    )
    assert outfile.read_bytes() == tempfile.read_bytes()


@run_in_thread
def test_upload_download_immutable_different_default_max_segment_size(alice, get_put_alias, tmpdir, request):
    """
    Tahoe-LAFS used to have a default max segment size of 128KB, and is now
    1MB.  Test that an upload created when 128KB was the default can be
    downloaded with 1MB as the default (i.e. old uploader, new downloader), and
    vice versa, (new uploader, old downloader).
    """
    tempfile = tmpdir.join("file")
    large_data = DATA * 100_000
    assert len(large_data) > 2 * 1024 * 1024
    with tempfile.open("wb") as f:
        f.write(large_data)

    def set_segment_size(segment_size):
        return blockingCallFromThread(
            reactor,
            lambda: alice.reconfigure_zfec(
                reactor,
                (1, 1, 1),
                None,
                max_segment_size=segment_size
            )
        )

    # 1. Upload file 1 with default segment size set to 1MB
    set_segment_size(1024 * 1024)
    cli(alice.process, "put", str(tempfile), "getput:seg1024kb")

    # 2. Download file 1 with default segment size set to 128KB
    set_segment_size(128 * 1024)
    assert large_data == check_output(
        ["tahoe", "--node-directory", alice.process.node_dir, "get", "getput:seg1024kb", "-"]
    )

    # 3. Upload file 2 with default segment size set to 128KB
    cli(alice.process, "put", str(tempfile), "getput:seg128kb")

    # 4. Download file 2 with default segment size set to 1MB
    set_segment_size(1024 * 1024)
    assert large_data == check_output(
        ["tahoe", "--node-directory", alice.process.node_dir, "get", "getput:seg128kb", "-"]
    )
