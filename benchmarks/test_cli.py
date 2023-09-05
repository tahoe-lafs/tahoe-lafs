"""Benchmarks for minimal `tahoe` CLI interactions."""

from subprocess import Popen, PIPE

import pytest

from integration.util import cli


@pytest.fixture(scope="session")
def cli_alias(client_node):
    cli(client_node.process, "create-alias", "cli")


def test_get_put_one_file(client_node, cli_alias, tmp_path):
    """
    Upload a file with ``tahoe put`` and then download it with ``tahoe get``,
    measuring the latency of both operations.
    """
    file_size = 1000  # parameterize later on
    file_path = tmp_path / "file"
    DATA = b"0123456789" * (file_size // 10)

    with file_path.open("wb") as f:
        f.write(DATA)
    cli(client_node.process, "put", str(file_path), "cli:tostdout")

    p = Popen(
        [
            "tahoe",
            "--node-directory",
            client_node.process.node_dir,
            "get",
            "cli:tostdout",
            "-",
        ],
        stdout=PIPE,
    )
    assert p.stdout.read() == DATA
    assert p.wait() == 0
