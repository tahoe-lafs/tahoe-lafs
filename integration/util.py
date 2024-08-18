"""
General functionality useful for the implementation of integration tests.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from typing_extensions import Literal
from tempfile import NamedTemporaryFile
import sys
import time
import json
from os import mkdir, environ
from os.path import exists, join, basename
from io import StringIO, BytesIO
from subprocess import check_output

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.defer import Deferred, succeed
from twisted.internet.protocol import ProcessProtocol
from twisted.internet.error import ProcessExitedAlready, ProcessDone
from twisted.internet.threads import deferToThread
from twisted.internet.interfaces import IProcessTransport, IReactorProcess

from attrs import frozen, evolve
import requests

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PrivateFormat,
    NoEncryption,
)

from paramiko.rsakey import RSAKey
from boltons.funcutils import wraps

from allmydata.util import base32
from allmydata.util.configutil import (
    get_config,
    set_config,
    write_config,
)
from allmydata import client
from allmydata.interfaces import DEFAULT_IMMUTABLE_MAX_SEGMENT_SIZE

import pytest_twisted


def block_with_timeout(deferred, reactor, timeout=120):
    """Block until Deferred has result, but timeout instead of waiting forever."""
    deferred.addTimeout(timeout, reactor)
    return pytest_twisted.blockon(deferred)


class _ProcessExitedProtocol(ProcessProtocol):
    """
    Internal helper that .callback()s on self.done when the process
    exits (for any reason).
    """

    def __init__(self):
        self.done = Deferred()

    def processEnded(self, reason):
        self.done.callback(None)


class ProcessFailed(Exception):
    """
    A subprocess has failed.

    :ivar ProcessTerminated reason: the original reason from .processExited

    :ivar StringIO output: all stdout and stderr collected to this point.
    """

    def __init__(self, reason, output):
        self.reason = reason
        self.output = output

    def __str__(self):
        return "<ProcessFailed: {}>:\n{}".format(self.reason, self.output)


class _CollectOutputProtocol(ProcessProtocol):
    """
    Internal helper. Collects all output (stdout + stderr) into
    self.output, and callback's on done with all of it after the
    process exits (for any reason).
    """

    def __init__(self, capture_stderr=True, stdin=None):
        self.done = Deferred()
        self.output = BytesIO()
        self.capture_stderr = capture_stderr
        self._stdin = stdin

    def connectionMade(self):
        if self._stdin is not None:
            self.transport.write(self._stdin)
            self.transport.closeStdin()

    def processEnded(self, reason):
        if not self.done.called:
            self.done.callback(self.output.getvalue())

    def processExited(self, reason):
        if not isinstance(reason.value, ProcessDone):
            self.done.errback(ProcessFailed(reason, self.output.getvalue()))

    def outReceived(self, data):
        self.output.write(data)

    def errReceived(self, data):
        if self.capture_stderr:
            self.output.write(data)


class _DumpOutputProtocol(ProcessProtocol):
    """
    Internal helper.
    """
    def __init__(self, f):
        self.done = Deferred()
        self._out = f if f is not None else sys.stdout

    def processEnded(self, reason):
        if not self.done.called:
            self.done.callback(None)

    def processExited(self, reason):
        if not isinstance(reason.value, ProcessDone):
            self.done.errback(reason)

    def outReceived(self, data):
        data = str(data, sys.stdout.encoding)
        self._out.write(data)

    def errReceived(self, data):
        data = str(data, sys.stdout.encoding)
        self._out.write(data)


class _MagicTextProtocol(ProcessProtocol):
    """
    Internal helper. Monitors all stdout looking for a magic string,
    and then .callback()s on self.done and .errback's if the process exits
    """

    def __init__(self, magic_text: str, name: str) -> None:
        self.magic_seen = Deferred()
        self.name = f"{name}: "
        self.exited = Deferred()
        self._magic_text = magic_text
        self._output = StringIO()

    def processEnded(self, reason):
        self.exited.callback(None)

    def outReceived(self, data):
        data = str(data, sys.stdout.encoding)
        for line in data.splitlines():
            sys.stdout.write(self.name + line + "\n")
        self._output.write(data)
        if not self.magic_seen.called and self._magic_text in self._output.getvalue():
            print("Saw '{}' in the logs".format(self._magic_text))
            self.magic_seen.callback(self)

    def errReceived(self, data):
        data = str(data, sys.stderr.encoding)
        for line in data.splitlines():
            sys.stdout.write(self.name + line + "\n")


def _cleanup_process_async(transport: IProcessTransport) -> None:
    """
    If the given process transport seems to still be associated with a
    running process, send a SIGTERM to that process.

    :param transport: The transport to use.

    :raise: ``ValueError`` if ``allow_missing`` is ``False`` and the transport
        has no process.
    """
    if transport.pid is None:
        # in cases of "restart", we will have registered a finalizer
        # that will kill the process -- but already explicitly killed
        # it (and then ran again) due to the "restart". So, if the
        # process is already killed, our job is done.
        print("Process already cleaned up and that's okay.")
        return
    print("signaling {} with TERM".format(transport.pid))
    try:
        transport.signalProcess('TERM')
    except ProcessExitedAlready:
        # The transport object thought it still had a process but the real OS
        # process has already exited.  That's fine.  We accomplished what we
        # wanted to.
        pass

def _cleanup_tahoe_process(tahoe_transport, exited):
    """
    Terminate the given process with a kill signal (SIGTERM on POSIX,
    TerminateProcess on Windows).

    :param tahoe_transport: The `IProcessTransport` representing the process.
    :param exited: A `Deferred` which fires when the process has exited.

    :return: After the process has exited.
    """
    from twisted.internet import reactor
    _cleanup_process_async(tahoe_transport)
    print(f"signaled, blocking on exit {exited}")
    block_with_timeout(exited, reactor)
    print("exited, goodbye")


def run_tahoe(reactor, request, *args, **kwargs):
    """
    Helper to run tahoe with optional coverage.

    :returns: a Deferred that fires when the command is done (or a
        ProcessFailed exception if it exits non-zero)
    """
    stdin = kwargs.get("stdin", None)
    protocol = _CollectOutputProtocol(stdin=stdin)
    process = _tahoe_runner_optional_coverage(protocol, reactor, request, args)
    process.exited = protocol.done
    return protocol.done


def _tahoe_runner_optional_coverage(proto, reactor, request, other_args):
    """
    Internal helper. Calls spawnProcess with `-m
    allmydata.scripts.runner` and `other_args`, optionally inserting a
    `--coverage` option if the `request` indicates we should.
    """
    if request.config.getoption('coverage', False):
        args = [sys.executable, '-b', '-m', 'coverage', 'run', '-m', 'allmydata.scripts.runner', '--coverage']
    else:
        args = [sys.executable, '-b', '-m', 'allmydata.scripts.runner']
    args += other_args
    return reactor.spawnProcess(
        proto,
        sys.executable,
        args,
        env=environ,
    )


class TahoeProcess(object):
    """
    A running Tahoe process, with associated information.
    """

    def __init__(self, process_transport, node_dir):
        self._process_transport = process_transport  # IProcessTransport instance
        self._node_dir = node_dir  # path

    @property
    def transport(self):
        return self._process_transport

    @property
    def node_dir(self):
        return self._node_dir

    def get_config(self):
        return client.read_config(
            self._node_dir,
            u"portnum",
        )

    def kill(self):
        """
        Kill the process, block until it's done.
        Does nothing if the process is already stopped (or never started).
        """
        print(f"TahoeProcess.kill({self.transport.pid} / {self.node_dir})")
        _cleanup_tahoe_process(self.transport, self.transport.exited)

    def kill_async(self):
        """
        Kill the process, return a Deferred that fires when it's done.
        Does nothing if the process is already stopped (or never started).
        """
        print(f"TahoeProcess.kill_async({self.transport.pid} / {self.node_dir})")
        _cleanup_process_async(self.transport)
        return self.transport.exited

    def restart_async(self, reactor: IReactorProcess, request: Any) -> Deferred:
        """
        Stop and then re-start the associated process.

        :return: A Deferred that fires after the new process is ready to
            handle requests.
        """
        d = self.kill_async()
        d.addCallback(lambda ignored: _run_node(reactor, self.node_dir, request, None))
        def got_new_process(proc):
            # Grab the new transport since the one we had before is no longer
            # valid after the stop/start cycle.
            self._process_transport = proc.transport
        d.addCallback(got_new_process)
        return d

    def __str__(self):
        return "<TahoeProcess in '{}'>".format(self._node_dir)


def _run_node(reactor, node_dir, request, magic_text):
    """
    Run a tahoe process from its node_dir.

    :returns: a TahoeProcess for this node
    """
    if magic_text is None:
        magic_text = "client running"
    protocol = _MagicTextProtocol(magic_text, basename(node_dir))

    # "tahoe run" is consistent across Linux/macOS/Windows, unlike the old
    # "start" command.
    transport = _tahoe_runner_optional_coverage(
        protocol,
        reactor,
        request,
        [
            '--eliot-destination', 'file:{}/logs/eliot.json'.format(node_dir),
            'run',
            node_dir,
        ],
    )
    transport.exited = protocol.exited

    tahoe_process = TahoeProcess(
        transport,
        node_dir,
    )

    request.addfinalizer(tahoe_process.kill)

    d = protocol.magic_seen
    d.addCallback(lambda ignored: tahoe_process)
    return d


def basic_node_configuration(request, flog_gatherer, node_dir: str):
    """
    Setup common configuration options for a node, given a ``pytest`` request
    fixture.
    """
    config_path = join(node_dir, 'tahoe.cfg')
    config = get_config(config_path)
    set_config(
        config,
        u'node',
        u'log_gatherer.furl',
        flog_gatherer,
    )
    force_foolscap = request.config.getoption("force_foolscap")
    assert force_foolscap in (True, False)
    set_config(
        config,
        'storage',
        'force_foolscap',
        str(force_foolscap),
    )
    set_config(
        config,
        'client',
        'force_foolscap',
        str(force_foolscap),
    )
    write_config(FilePath(config_path), config)


def _create_node(reactor, request, temp_dir, introducer_furl, flog_gatherer, name, web_port,
                 storage=True,
                 magic_text=None,
                 needed=2,
                 happy=3,
                 total=4):
    """
    Helper to create a single node, run it and return the instance
    spawnProcess returned (ITransport)
    """
    node_dir = join(temp_dir, name)
    if web_port is None:
        web_port = ''
    if exists(node_dir):
        created_d = succeed(None)
    else:
        print("creating: {}".format(node_dir))
        mkdir(node_dir)
        done_proto = _ProcessExitedProtocol()
        args = [
            'create-node',
            '--nickname', name,
            '--introducer', introducer_furl,
            '--hostname', 'localhost',
            '--listen', 'tcp',
            '--webport', web_port,
            '--shares-needed', str(needed),
            '--shares-happy', str(happy),
            '--shares-total', str(total),
            '--helper',
        ]
        if not storage:
            args.append('--no-storage')
        args.append(node_dir)

        _tahoe_runner_optional_coverage(done_proto, reactor, request, args)
        created_d = done_proto.done

        def created(_):
            basic_node_configuration(request, flog_gatherer.furl, node_dir)
        created_d.addCallback(created)

    d = Deferred()
    d.callback(None)
    d.addCallback(lambda _: created_d)
    d.addCallback(lambda _: _run_node(reactor, node_dir, request, magic_text))
    return d


class UnwantedFilesException(Exception):
    """
    While waiting for some files to appear, some undesired files
    appeared instead (or in addition).
    """
    def __init__(self, waiting, unwanted):
        super(UnwantedFilesException, self).__init__(
            u"While waiting for '{}', unwanted files appeared: {}".format(
                waiting,
                u', '.join(unwanted),
            )
        )


class ExpectedFileMismatchException(Exception):
    """
    A file or files we wanted weren't found within the timeout.
    """
    def __init__(self, path, timeout):
        super(ExpectedFileMismatchException, self).__init__(
            u"Contents of '{}' mismatched after {}s".format(path, timeout),
        )


class ExpectedFileUnfoundException(Exception):
    """
    A file or files we expected to find didn't appear within the
    timeout.
    """
    def __init__(self, path, timeout):
        super(ExpectedFileUnfoundException, self).__init__(
            u"Didn't find '{}' after {}s".format(path, timeout),
        )



class FileShouldVanishException(Exception):
    """
    A file or files we expected to disappear did not within the
    timeout
    """
    def __init__(self, path, timeout):
        super(FileShouldVanishException, self).__init__(
            u"'{}' still exists after {}s".format(path, timeout),
        )


def run_in_thread(f):
    """Decorator for integration tests that runs code in a thread.

    Because we're using pytest_twisted, tests that rely on the reactor are
    expected to return a Deferred and use async APIs so the reactor can run.

    In the case of the integration test suite, it launches nodes in the
    background using Twisted APIs.  The nodes stdout and stderr is read via
    Twisted code.  If the reactor doesn't run, reads don't happen, and
    eventually the buffers fill up, and the nodes block when they try to flush
    logs.

    We can switch to Twisted APIs (treq instead of requests etc.), but
    sometimes it's easier or expedient to just have a blocking test.  So this
    decorator allows you to run the test in a thread, and the reactor can keep
    running in the main thread.

    See https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3597 for tracking bug.
    """
    @wraps(f)
    def test(*args, **kwargs):
        return deferToThread(lambda: f(*args, **kwargs))
    return test


def await_file_contents(path, contents, timeout=15, error_if=None):
    """
    wait up to `timeout` seconds for the file at `path` (any path-like
    object) to have the exact content `contents`.

    :param error_if: if specified, a list of additional paths; if any
        of these paths appear an Exception is raised.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        print("  waiting for '{}'".format(path))
        if error_if and any([exists(p) for p in error_if]):
            raise UnwantedFilesException(
                waiting=path,
                unwanted=[p for p in error_if if exists(p)],
            )
        if exists(path):
            try:
                with open(path, 'r') as f:
                    current = f.read()
            except IOError:
                print("IOError; trying again")
            else:
                if current == contents:
                    return True
                print("  file contents still mismatched")
                print("  wanted: {}".format(contents.replace('\n', ' ')))
                print("     got: {}".format(current.replace('\n', ' ')))
        time.sleep(1)
    if exists(path):
        raise ExpectedFileMismatchException(path, timeout)
    raise ExpectedFileUnfoundException(path, timeout)


def await_files_exist(paths, timeout=15, await_all=False):
    """
    wait up to `timeout` seconds for any of the paths to exist; when
    any exist, a list of all found filenames is returned. Otherwise,
    an Exception is raised
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        print("  waiting for: {}".format(' '.join(paths)))
        found = [p for p in paths if exists(p)]
        print("found: {}".format(found))
        if await_all:
            if len(found) == len(paths):
                return found
        else:
            if len(found) > 0:
                return found
        time.sleep(1)
    if await_all:
        nice_paths = ' and '.join(paths)
    else:
        nice_paths = ' or '.join(paths)
    raise ExpectedFileUnfoundException(nice_paths, timeout)


def await_file_vanishes(path, timeout=10):
    start_time = time.time()
    while time.time() - start_time < timeout:
        print("  waiting for '{}' to vanish".format(path))
        if not exists(path):
            return
        time.sleep(1)
    raise FileShouldVanishException(path, timeout)


def cli(node, *argv):
    """
    Run a tahoe CLI subcommand for a given node in a blocking manner, returning
    the output.
    """
    arguments = ["tahoe", '--node-directory', node.node_dir]
    return check_output(arguments + list(argv))


def node_url(node_dir, uri_fragment):
    """
    Create a fully qualified URL by reading config from `node_dir` and
    adding the `uri_fragment`
    """
    with open(join(node_dir, "node.url"), "r") as f:
        base = f.read().strip()
    url = base + uri_fragment
    return url


def _check_status(response):
    """
    Check the response code is a 2xx (raise an exception otherwise)
    """
    if response.status_code < 200 or response.status_code >= 300:
        raise ValueError(
            "Expected a 2xx code, got {}".format(response.status_code)
        )


def web_get(tahoe, uri_fragment, **kwargs):
    """
    Make a GET request to the webport of `tahoe` (a `TahoeProcess`,
    usually from a fixture (e.g. `alice`). This will look like:
    `http://localhost:<webport>/<uri_fragment>`. All `kwargs` are
    passed on to `requests.get`
    """
    url = node_url(tahoe.node_dir, uri_fragment)
    resp = requests.get(url, **kwargs)
    _check_status(resp)
    return resp.content


def web_post(tahoe, uri_fragment, **kwargs):
    """
    Make a POST request to the webport of `node` (a `TahoeProcess,
    usually from a fixture e.g. `alice`). This will look like:
    `http://localhost:<webport>/<uri_fragment>`. All `kwargs` are
    passed on to `requests.post`
    """
    url = node_url(tahoe.node_dir, uri_fragment)
    resp = requests.post(url, **kwargs)
    _check_status(resp)
    return resp.content


@run_in_thread
def await_client_ready(tahoe, timeout=10, liveness=60*2, minimum_number_of_servers=1):
    """
    Uses the status API to wait for a client-type node (in `tahoe`, a
    `TahoeProcess` instance usually from a fixture e.g. `alice`) to be
    'ready'. A client is deemed ready if:

      - it answers `http://<node_url>/statistics/?t=json/`
      - there is at least one storage-server connected (configurable via
        ``minimum_number_of_servers``)
      - every storage-server has a "last_received_data" and it is
        within the last `liveness` seconds

    We will try for up to `timeout` seconds for the above conditions
    to be true. Otherwise, an exception is raised
    """
    start = time.time()
    while (time.time() - start) < float(timeout):
        try:
            data = web_get(tahoe, u"", params={u"t": u"json"})
            js = json.loads(data)
        except Exception as e:
            print("waiting because '{}'".format(e))
            time.sleep(1)
            continue
        servers = js['servers']

        if len(servers) < minimum_number_of_servers:
            print(f"waiting because {servers} is fewer than required ({minimum_number_of_servers})")
            time.sleep(1)
            continue

        now = time.time()
        server_times = [
            server['last_received_data']
            for server
            in servers
            if server['last_received_data'] is not None
        ]
        print(
            f"Now: {time.ctime(now)}\n"
            f"Liveness required: {liveness}\n"
            f"Server last-received-data: {[time.ctime(s) for s in server_times]}\n"
            f"Server ages: {[now - s for s in server_times]}\n"
        )

        # check that all times are 'recent enough' (it's OK if _some_ servers
        # are down, we just want to make sure a sufficient number are up)
        alive = [t for t in server_times if now - t <= liveness]
        if len(alive) < minimum_number_of_servers:
            print(
                f"waiting because we found {len(alive)} servers "
                f"and want {minimum_number_of_servers}"
            )
            time.sleep(1)
            continue

        # we have a status with at least one server, and all servers
        # have been contacted recently
        return True
    # we only fall out of the loop when we've timed out
    raise RuntimeError(
        "Waited {} seconds for {} to be 'ready' but it never was".format(
            timeout,
            tahoe,
        )
    )


def generate_ssh_key(path):
    """Create a new SSH private/public key pair."""
    key = RSAKey.generate(2048)
    key.write_private_key_file(path)
    with open(path + ".pub", "wb") as f:
        s = "%s %s" % (key.get_name(), key.get_base64())
        f.write(s.encode("ascii"))


@frozen
class CHK:
    """
    Represent the CHK encoding sufficiently to run a ``tahoe put`` command
    using it.
    """
    kind = "chk"
    max_shares = 256

    def customize(self) -> CHK:
        # Nothing to do.
        return self

    @classmethod
    def load(cls, params: None) -> CHK:
        assert params is None
        return cls()

    def to_json(self) -> None:
        return None

    @contextmanager
    def to_argv(self) -> None:
        yield []

@frozen
class SSK:
    """
    Represent the SSK encodings (SDMF and MDMF) sufficiently to run a
    ``tahoe put`` command using one of them.
    """
    kind = "ssk"

    # SDMF and MDMF encode share counts (N and k) into the share itself as an
    # unsigned byte.  They could have encoded (share count - 1) to fit the
    # full range supported by ZFEC into the unsigned byte - but they don't.
    # So 256 is inaccessible to those formats and we set the upper bound at
    # 255.
    max_shares = 255

    name: Literal["sdmf", "mdmf"]
    key: None | bytes

    @classmethod
    def load(cls, params: dict) -> SSK:
        assert params.keys() == {"format", "mutable", "key"}
        return cls(params["format"], params["key"].encode("ascii"))
    def customize(self) -> SSK:
        """
        Return an SSK with a newly generated random RSA key.
        """
        return evolve(self, key=generate_rsa_key())

    def to_json(self) -> dict[str, str]:
        return {
            "format": self.name,
            "mutable": None,
            "key": self.key.decode("ascii"),
        }

    @contextmanager
    def to_argv(self) -> None:
        with NamedTemporaryFile() as f:
            f.write(self.key)
            f.flush()
            yield [f"--format={self.name}", "--mutable", f"--private-key-path={f.name}"]


def upload(alice: TahoeProcess, fmt: CHK | SSK, data: bytes) -> str:
    """
    Upload the given data to the given node.

    :param alice: The node to upload to.

    :param fmt: The name of the format for the upload.  CHK, SDMF, or MDMF.

    :param data: The data to upload.

    :return: The capability for the uploaded data.
    """

    with NamedTemporaryFile() as f:
        f.write(data)
        f.flush()
        with fmt.to_argv() as fmt_argv:
            argv = [alice.process, "put"] + fmt_argv + [f.name]
            return cli(*argv).decode("utf-8").strip()


async def reconfigure(reactor, request, node: TahoeProcess,
                      params: tuple[int, int, int],
                      convergence: None | bytes,
                      max_segment_size: None | int = None) -> None:
    """
    Reconfigure a Tahoe-LAFS node with different ZFEC parameters and
    convergence secret.

    TODO This appears to have issues on Windows.

    If the current configuration is different from the specified
    configuration, the node will be restarted so it takes effect.

    :param reactor: A reactor to use to restart the process.
    :param request: The pytest request object to use to arrange process
        cleanup.
    :param node: The Tahoe-LAFS node to reconfigure.
    :param params: The ``happy``, ``needed``, and ``total`` ZFEC encoding
      parameters.
    :param convergence: If given, the convergence secret.  If not given, the
        existing convergence secret will be left alone.

    :return: ``None`` after the node configuration has been rewritten, the
        node has been restarted, and the node is ready to provide service.
    """
    happy, needed, total = params
    config = node.get_config()

    changed = False
    cur_happy = int(config.get_config("client", "shares.happy"))
    cur_needed = int(config.get_config("client", "shares.needed"))
    cur_total = int(config.get_config("client", "shares.total"))

    if (happy, needed, total) != (cur_happy, cur_needed, cur_total):
        changed = True
        config.set_config("client", "shares.happy", str(happy))
        config.set_config("client", "shares.needed", str(needed))
        config.set_config("client", "shares.total", str(total))

    if convergence is not None:
        cur_convergence = config.get_private_config("convergence").encode("ascii")
        if base32.a2b(cur_convergence) != convergence:
            changed = True
            config.write_private_config("convergence", base32.b2a(convergence))

    if max_segment_size is not None:
        cur_segment_size = int(config.get_config("client", "shares._max_immutable_segment_size_for_testing", DEFAULT_IMMUTABLE_MAX_SEGMENT_SIZE))
        if cur_segment_size != max_segment_size:
            changed = True
            config.set_config(
                "client",
                "shares._max_immutable_segment_size_for_testing",
                str(max_segment_size)
            )

    if changed:
        # restart the node
        print(f"Restarting {node.node_dir} for ZFEC reconfiguration")
        await node.restart_async(reactor, request)
        print("Restarted.  Waiting for ready state.")
        await await_client_ready(node)
        print("Ready.")
    else:
        print("Config unchanged, not restarting.")


def generate_rsa_key() -> bytes:
    """
    Generate a 2048 bit RSA key suitable for use with SSKs.
    """
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    ).private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=NoEncryption(),
    )
