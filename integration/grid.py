"""
Classes which directly represent various kinds of Tahoe processes
that co-operate to for "a Grid".

These methods and objects are used by conftest.py fixtures but may
also be used as direct helpers for tests that don't want to (or can't)
rely on 'the' global grid as provided by fixtures like 'alice' or
'storage_servers'.
"""

from os import mkdir, listdir
from os.path import join, exists
from json import loads
from tempfile import mktemp
from time import sleep

from eliot import (
    log_call,
)

from foolscap.furl import (
    decode_furl,
)

from twisted.python.procutils import which
from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
    Deferred,
)
from twisted.internet.task import (
    deferLater,
)
from twisted.internet.interfaces import (
    IProcessTransport,
    IProcessProtocol,
)
from twisted.internet.error import ProcessTerminated

from allmydata.util.attrs_provides import (
    provides,
)
from allmydata.node import read_config
from .util import (
    _CollectOutputProtocol,
    _MagicTextProtocol,
    _DumpOutputProtocol,
    _ProcessExitedProtocol,
    _run_node,
    _cleanup_tahoe_process,
    _tahoe_runner_optional_coverage,
    TahoeProcess,
    await_client_ready,
    generate_ssh_key,
    cli,
    reconfigure,
    _create_node,
)

import attr
import pytest_twisted


# currently, we pass a "request" around a bunch but it seems to only
# be for addfinalizer() calls.
# - is "keeping" a request like that okay? What if it's a session-scoped one?
#   (i.e. in Grid etc)
# - maybe limit to "a callback to hang your cleanup off of" (instead of request)?


@attr.s
class FlogGatherer(object):
    """
    Flog Gatherer process.
    """
    process = attr.ib(
        validator=provides(IProcessTransport)
    )
    protocol = attr.ib(
        validator=provides(IProcessProtocol)
    )
    furl = attr.ib()


@inlineCallbacks
def create_flog_gatherer(reactor, request, temp_dir, flog_binary):
    out_protocol = _CollectOutputProtocol()
    gather_dir = join(temp_dir, 'flog_gather')
    reactor.spawnProcess(
        out_protocol,
        flog_binary,
        (
            'flogtool', 'create-gatherer',
            '--location', 'tcp:localhost:3117',
            '--port', '3117',
            gather_dir,
        )
    )
    yield out_protocol.done

    twistd_protocol = _MagicTextProtocol("Gatherer waiting at", "gatherer")
    twistd_process = reactor.spawnProcess(
        twistd_protocol,
        which('twistd')[0],
        (
            'twistd', '--nodaemon', '--python',
            join(gather_dir, 'gatherer.tac'),
        ),
        path=gather_dir,
    )
    yield twistd_protocol.magic_seen

    def cleanup():
        _cleanup_tahoe_process(twistd_process, twistd_protocol.exited)

        flog_file = mktemp('.flog_dump')
        flog_protocol = _DumpOutputProtocol(open(flog_file, 'w'))
        flog_dir = join(temp_dir, 'flog_gather')
        flogs = [x for x in listdir(flog_dir) if x.endswith('.flog')]

        print("Dumping {} flogtool logfiles to '{}'".format(len(flogs), flog_file))
        for flog_path in flogs:
            reactor.spawnProcess(
                flog_protocol,
                flog_binary,
                (
                    'flogtool', 'dump', join(temp_dir, 'flog_gather', flog_path)
                ),
            )
        print("Waiting for flogtool to complete")
        try:
            pytest_twisted.blockon(flog_protocol.done)
        except ProcessTerminated as e:
            print("flogtool exited unexpectedly: {}".format(str(e)))
        print("Flogtool completed")

    request.addfinalizer(cleanup)

    with open(join(gather_dir, 'log_gatherer.furl'), 'r') as f:
        furl = f.read().strip()
    returnValue(
        FlogGatherer(
            protocol=twistd_protocol,
            process=twistd_process,
            furl=furl,
        )
    )


@attr.s
class StorageServer(object):
    """
    Represents a Tahoe Storage Server
    """

    process = attr.ib(
        validator=attr.validators.instance_of(TahoeProcess)
    )
    protocol = attr.ib(
        validator=provides(IProcessProtocol)
    )

    @inlineCallbacks
    def restart(self, reactor, request):
        """
        re-start our underlying process by issuing a TERM, waiting and
        then running again. await_client_ready() will be done as well

        Note that self.process and self.protocol will be new instances
        after this.
        """
        self.process.transport.signalProcess('TERM')
        yield self.protocol.exited
        self.process = yield _run_node(
            reactor, self.process.node_dir, request, None,
        )
        self.protocol = self.process.transport.proto
        yield await_client_ready(self.process)


@inlineCallbacks
def create_storage_server(reactor, request, temp_dir, introducer, flog_gatherer, name, web_port,
                          needed=2, happy=3, total=4):
    """
    Create a new storage server
    """
    node_process = yield _create_node(
        reactor, request, temp_dir, introducer.furl, flog_gatherer,
        name, web_port, storage=True, needed=needed, happy=happy, total=total,
    )
    storage = StorageServer(
        process=node_process,
        # node_process is a TahoeProcess. its transport is an
        # IProcessTransport.  in practice, this means it is a
        # twisted.internet._baseprocess.BaseProcess. BaseProcess records the
        # process protocol as its proto attribute.
        protocol=node_process.transport.proto,
    )
    returnValue(storage)


@attr.s
class Client(object):
    """
    Represents a Tahoe client
    """

    process = attr.ib(
        validator=attr.validators.instance_of(TahoeProcess)
    )
    protocol = attr.ib(
        validator=provides(IProcessProtocol)
    )
    request = attr.ib()  # original request, for addfinalizer()

## XXX convenience? or confusion?
#    @property
#    def node_dir(self):
#        return self.process.node_dir

    @inlineCallbacks
    def reconfigure_zfec(self, reactor, zfec_params, convergence=None, max_segment_size=None):
        """
        Reconfigure the ZFEC parameters for this node
        """
        # XXX this is a stop-gap to keep tests running "as is"
        # -> we should fix the tests so that they create a new client
        #    in the grid with the required parameters, instead of
        #    re-configuring Alice (or whomever)

        rtn = yield Deferred.fromCoroutine(
            reconfigure(reactor, self.request, self.process, zfec_params, convergence, max_segment_size)
        )
        return rtn

    @inlineCallbacks
    def restart(self, reactor, request, servers=1):
        """
        re-start our underlying process by issuing a TERM, waiting and
        then running again.

        :param int servers: number of server connections we will wait
            for before being 'ready'

        Note that self.process and self.protocol will be new instances
        after this.
        """
        # XXX similar to above, can we make this return a new instance
        # instead of mutating?
        self.process.transport.signalProcess('TERM')
        yield self.protocol.exited
        process = yield _run_node(
            reactor, self.process.node_dir, request, None,
        )
        self.process = process
        self.protocol = self.process.transport.proto
        yield await_client_ready(self.process, minimum_number_of_servers=servers)

    @inlineCallbacks
    def add_sftp(self, reactor, request):
        """
        """
        # if other things need to add or change configuration, further
        # refactoring could be useful here (i.e. move reconfigure
        # parts to their own functions)

        # XXX why do we need an alias?
        # 1. Create a new RW directory cap:
        cli(self.process, "create-alias", "test")
        rwcap = loads(cli(self.process, "list-aliases", "--json"))["test"]["readwrite"]

        # 2. Enable SFTP on the node:
        host_ssh_key_path = join(self.process.node_dir, "private", "ssh_host_rsa_key")
        sftp_client_key_path = join(self.process.node_dir, "private", "ssh_client_rsa_key")
        accounts_path = join(self.process.node_dir, "private", "accounts")
        with open(join(self.process.node_dir, "tahoe.cfg"), "a") as f:
            f.write(
                ("\n\n[sftpd]\n"
                 "enabled = true\n"
                 "port = tcp:8022:interface=127.0.0.1\n"
                 "host_pubkey_file = {ssh_key_path}.pub\n"
                 "host_privkey_file = {ssh_key_path}\n"
                 "accounts.file = {accounts_path}\n").format(
                     ssh_key_path=host_ssh_key_path,
                     accounts_path=accounts_path,
                 )
            )
        generate_ssh_key(host_ssh_key_path)

        # 3. Add a SFTP access file with an SSH key for auth.
        generate_ssh_key(sftp_client_key_path)
        # Pub key format is "ssh-rsa <thekey> <username>". We want the key.
        with open(sftp_client_key_path + ".pub") as pubkey_file:
            ssh_public_key = pubkey_file.read().strip().split()[1]
        with open(accounts_path, "w") as f:
            f.write(
                "alice-key ssh-rsa {ssh_public_key} {rwcap}\n".format(
                    rwcap=rwcap,
                    ssh_public_key=ssh_public_key,
                )
            )

        # 4. Restart the node with new SFTP config.
        print("restarting for SFTP")
        yield self.restart(reactor, request)
        print("restart done")
        # XXX i think this is broken because we're "waiting for ready" during first bootstrap? or something?


@inlineCallbacks
def create_client(reactor, request, temp_dir, introducer, flog_gatherer, name, web_port,
                  needed=2, happy=3, total=4):
    """
    Create a new storage server
    """
    from .util import _create_node
    node_process = yield _create_node(
        reactor, request, temp_dir, introducer.furl, flog_gatherer,
        name, web_port, storage=False, needed=needed, happy=happy, total=total,
    )
    returnValue(
        Client(
            process=node_process,
            protocol=node_process.transport.proto,
            request=request,
        )
    )


@attr.s
class Introducer(object):
    """
    Reprsents a running introducer
    """

    process = attr.ib(
        validator=attr.validators.instance_of(TahoeProcess)
    )
    protocol = attr.ib(
        validator=provides(IProcessProtocol)
    )
    furl = attr.ib()


def _validate_furl(furl_fname):
    """
    Opens and validates a fURL, ensuring location hints.
    :returns: the furl
    :raises: ValueError if no location hints
    """
    while not exists(furl_fname):
        print("Don't see {} yet".format(furl_fname))
        sleep(.1)
    furl = open(furl_fname, 'r').read()
    tubID, location_hints, name = decode_furl(furl)
    if not location_hints:
        # If there are no location hints then nothing can ever possibly
        # connect to it and the only thing that can happen next is something
        # will hang or time out.  So just give up right now.
        raise ValueError(
            "Introducer ({!r}) fURL has no location hints!".format(
                furl,
            ),
        )
    return furl


@inlineCallbacks
@log_call(
    action_type=u"integration:introducer",
    include_args=["temp_dir", "flog_gatherer"],
    include_result=False,
)
def create_introducer(reactor, request, temp_dir, flog_gatherer, port):
    """
    Run a new Introducer and return an Introducer instance.
    """
    intro_dir = join(temp_dir, 'introducer{}'.format(port))

    if not exists(intro_dir):
        mkdir(intro_dir)
        done_proto = _ProcessExitedProtocol()
        _tahoe_runner_optional_coverage(
            done_proto,
            reactor,
            request,
            (
                'create-introducer',
                '--listen=tcp',
                '--hostname=localhost',
                intro_dir,
            ),
        )
        yield done_proto.done

    config = read_config(intro_dir, "tub.port")
    config.set_config("node", "nickname", f"introducer-{port}")
    config.set_config("node", "web.port", f"{port}")
    config.set_config("node", "log_gatherer.furl", flog_gatherer.furl)

    # on windows, "tahoe start" means: run forever in the foreground,
    # but on linux it means daemonize. "tahoe run" is consistent
    # between platforms.
    protocol = _MagicTextProtocol('introducer running', "introducer")
    transport = _tahoe_runner_optional_coverage(
        protocol,
        reactor,
        request,
        (
            'run',
            intro_dir,
        ),
    )

    def clean():
        return _cleanup_tahoe_process(transport, protocol.exited)
    request.addfinalizer(clean)

    yield protocol.magic_seen

    furl_fname = join(intro_dir, 'private', 'introducer.furl')
    while not exists(furl_fname):
        print("Don't see {} yet".format(furl_fname))
        yield deferLater(reactor, .1, lambda: None)
    furl = _validate_furl(furl_fname)

    returnValue(
        Introducer(
            process=TahoeProcess(transport, intro_dir),
            protocol=protocol,
            furl=furl,
        )
    )


@attr.s
class Grid(object):
    """
    Represents an entire Tahoe Grid setup

    A Grid includes an Introducer, Flog Gatherer and some number of
    Storage Servers. Optionally includes Clients.
    """

    _reactor = attr.ib()
    _request = attr.ib()
    _temp_dir = attr.ib()
    _port_allocator = attr.ib()
    introducer = attr.ib()
    flog_gatherer = attr.ib()
    storage_servers = attr.ib(factory=list)
    clients = attr.ib(factory=dict)

    @storage_servers.validator
    def check(self, attribute, value):
        for server in value:
            if not isinstance(server, StorageServer):
                raise ValueError(
                    "storage_servers must be StorageServer"
                )

    @inlineCallbacks
    def add_storage_node(self):
        """
        Creates a new storage node, returns a StorageServer instance
        (which will already be added to our .storage_servers list)
        """
        port = yield self._port_allocator()
        print("make {}".format(port))
        name = 'node{}'.format(port)
        web_port = 'tcp:{}:interface=localhost'.format(port)
        server = yield create_storage_server(
            self._reactor,
            self._request,
            self._temp_dir,
            self.introducer,
            self.flog_gatherer,
            name,
            web_port,
        )
        self.storage_servers.append(server)
        returnValue(server)

    @inlineCallbacks
    def add_client(self, name, needed=2, happy=3, total=4):
        """
        Create a new client node
        """
        port = yield self._port_allocator()
        web_port = 'tcp:{}:interface=localhost'.format(port)
        client = yield create_client(
            self._reactor,
            self._request,
            self._temp_dir,
            self.introducer,
            self.flog_gatherer,
            name,
            web_port,
            needed=needed,
            happy=happy,
            total=total,
        )
        self.clients[name] = client
        yield await_client_ready(client.process)
        returnValue(client)


# A grid is now forever tied to its original 'request' which is where
# it must hang finalizers off of. The "main" one is a session-level
# fixture so it'll live the life of the tests but it could be
# per-function Grid too.
@inlineCallbacks
def create_grid(reactor, request, temp_dir, flog_gatherer, port_allocator):
    """
    Create a new grid. This will have one Introducer but zero
    storage-servers or clients; those must be added by a test or
    subsequent fixtures.
    """
    intro_port = yield port_allocator()
    introducer = yield create_introducer(reactor, request, temp_dir, flog_gatherer, intro_port)
    grid = Grid(
        reactor,
        request,
        temp_dir,
        port_allocator,
        introducer,
        flog_gatherer,
    )
    returnValue(grid)
