"""Benchmarks for minimal `tahoe` CLI interactions."""

from subprocess import Popen, PIPE

import pytest

from integration.util import cli


@pytest.fixture(scope="module", autouse=True)
def cli_alias(client_node):
    cli(client_node.process, "create-alias", "cli")


@pytest.mark.parametrize("file_size", [1000, 100_000, 1_000_000, 10_000_000])
def test_get_put_files_sequentially(
    file_size,
    client_node,
    tahoe_benchmarker,
    number_of_nodes,
    capsys,
):
    """
    Upload 5 files with ``tahoe put`` and then download them with ``tahoe
    get``, measuring the latency of both operations.  We do multiple uploads
    and downloads to try to reduce noise.
    """
    DATA = b"0123456789" * (file_size // 10)

    with tahoe_benchmarker.record(
        capsys, "cli-put-5-file-sequentially", file_size=file_size, number_of_nodes=number_of_nodes
    ):
        for i in range(5):
            p = Popen(
                [
                    "tahoe",
                    "--node-directory",
                    client_node.process.node_dir,
                    "put",
                    "-",
                    f"cli:get_put_files_sequentially{i}",
                ],
                stdin=PIPE,
            )
            p.stdin.write(DATA)
            p.stdin.write(str(i).encode("ascii"))
            p.stdin.close()
            assert p.wait() == 0

    with tahoe_benchmarker.record(
        capsys, "cli-get-5-files-sequentially", file_size=file_size, number_of_nodes=number_of_nodes
    ):
        for i in range(5):
            p = Popen(
                [
                    "tahoe",
                    "--node-directory",
                    client_node.process.node_dir,
                    "get",
                    f"cli:get_put_files_sequentially{i}",
                    "-",
                ],
                stdout=PIPE,
            )
            assert p.stdout.read() == DATA + str(i).encode("ascii")
            assert p.wait() == 0
