"""
pytest infrastructure for benchmarks.

The number of nodes is parameterized via a --number-of-nodes CLI option added
to pytest.
"""

from os.path import abspath
from shutil import which, rmtree
from tempfile import mkdtemp
from pathlib import Path
from contextlib import contextmanager
from time import time

import pytest
import pytest_twisted

from twisted.internet import reactor
from twisted.internet.defer import DeferredList, succeed

from allmydata.util.iputil import allocate_tcp_port

from integration.grid import Client, create_grid, create_flog_gatherer


def pytest_addoption(parser):
    parser.addoption(
        "--number-of-nodes",
        action="append",
        default=[],
        type=int,
        help="list of number_of_nodes to benchmark against",
    )
    # Required to be compatible with integration.util code that we indirectly
    # depend on, but also might be useful.
    parser.addoption(
        "--force-foolscap",
        action="store_true",
        default=False,
        dest="force_foolscap",
        help=(
            "If set, force Foolscap only for the storage protocol. "
            + "Otherwise HTTP will be used."
        ),
    )


def pytest_generate_tests(metafunc):
    # Make number_of_nodes accessible as a parameterized fixture:
    if "number_of_nodes" in metafunc.fixturenames:
        metafunc.parametrize(
            "number_of_nodes",
            metafunc.config.getoption("number_of_nodes"),
            scope="session",
        )


def port_allocator():
    port = allocate_tcp_port()
    return succeed(port)


@pytest.fixture(scope="session")
def grid(request):
    """
    Provides a new Grid with a single Introducer and flog-gathering process.

    Notably does _not_ provide storage servers; use the storage_nodes
    fixture if your tests need a Grid that can be used for puts / gets.
    """
    tmp_path = mkdtemp(prefix="tahoe-benchmark")
    request.addfinalizer(lambda: rmtree(tmp_path))
    flog_binary = which("flogtool")
    flog_gatherer = pytest_twisted.blockon(
        create_flog_gatherer(reactor, request, tmp_path, flog_binary)
    )
    g = pytest_twisted.blockon(
        create_grid(reactor, request, tmp_path, flog_gatherer, port_allocator)
    )
    return g


@pytest.fixture(scope="session")
def storage_nodes(grid, number_of_nodes):
    nodes_d = []
    for _ in range(number_of_nodes):
        nodes_d.append(grid.add_storage_node())

    nodes_status = pytest_twisted.blockon(DeferredList(nodes_d))
    for ok, value in nodes_status:
        assert ok, "Storage node creation failed: {}".format(value)
    return grid.storage_servers


@pytest.fixture(scope="session")
def client_node(request, grid, storage_nodes, number_of_nodes) -> Client:
    """
    Create a grid client node with number of shares matching number of nodes.
    """
    client_node = pytest_twisted.blockon(
        grid.add_client(
            "client_node",
            needed=number_of_nodes,
            happy=number_of_nodes,
            total=number_of_nodes,
        )
    )
    print(f"Client node pid: {client_node.process.transport.pid}")
    return client_node


class Benchmarker:
    """Keep track of benchmarking results."""

    @contextmanager
    def record(self, name, **parameters):
        """Record the timing of running some code, if it succeeds."""
        start = time()
        yield
        elapsed = time() - start
        # For now we just print the outcome:
        parameters = " ".join(f"{k}={v}" for (k, v) in parameters.items())
        print(f"BENCHMARK RESULT: {name} {parameters} elapsed {elapsed} secs")


@pytest.fixture(scope="session")
def tahoe_benchmarker():
    return Benchmarker()
