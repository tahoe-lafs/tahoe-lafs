"""
Classes which directly represent various kinds of Tahoe processes
that co-operate to for "a Grid".

These methods and objects are used by conftest.py fixtures but may
also be used as direct helpers for tests that don't want to (or can't)
rely on 'the' global grid as provided by fixtures like 'alice' or
'storage_servers'.
"""

from os import mkdir, listdir, environ
from os.path import join, exists
from tempfile import mkdtemp, mktemp

from eliot import (
    log_call,
)

from twisted.python.procutils import which
from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
    maybeDeferred,
)
from twisted.internet.task import (
    deferLater,
)
from twisted.internet.interfaces import (
    IProcessTransport,
    IProcessProtocol,
    IProtocol,
)
from twisted.internet.endpoints import (
    TCP4ServerEndpoint,
)
from twisted.internet.protocol import (
    Factory,
    Protocol,
)

from util import (
    _CollectOutputProtocol,
    _MagicTextProtocol,
    _DumpOutputProtocol,
    _ProcessExitedProtocol,
    _create_node,
    _run_node,
    _cleanup_tahoe_process,
    _tahoe_runner_optional_coverage,
    TahoeProcess,
    await_client_ready,
)

import attr
import pytest_twisted


# further directions:
# - "Grid" is unused, basically -- tie into the rest?
#   - could make a Grid instance mandatory for create_* calls
#   - could instead make create_* calls methods of Grid
# - Bring more 'util' or 'conftest' code into here
#    - stop()/start()/restart() methods on StorageServer etc
#    - more-complex stuff like config changes (which imply a restart too)?


@attr.s
class FlogGatherer(object):
    """
    Flog Gatherer process.
    """

    process = attr.ib(
        validator=attr.validators.provides(IProcessTransport)
    )
    protocol = attr.ib(
        validator=attr.validators.provides(IProcessProtocol)
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

    twistd_protocol = _MagicTextProtocol("Gatherer waiting at")
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
        reactor.spawnProcess(
            flog_protocol,
            flog_binary,
            (
                'flogtool', 'dump', join(temp_dir, 'flog_gather', flogs[0])
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
        validator=attr.validators.provides(IProcessProtocol)
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
        self.protocol = self.process.transport._protocol

    @inlineCallbacks
    def run(


@inlineCallbacks
def create_storage_server(reactor, request, temp_dir, introducer, flog_gatherer, name, web_port,
                          needed=2, happy=3, total=4):
    """
    Create a new storage server
    """
    from util import _create_node
    node_process = yield _create_node(
        reactor, request, temp_dir, introducer.furl, flog_gatherer,
        name, web_port, storage=True, needed=needed, happy=happy, total=total,
    )
    storage = StorageServer(
        process=node_process,
        protocol=node_process.transport._protocol,
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
        validator=attr.validators.provides(IProcessProtocol)
    )

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
        self.process.transport.signalProcess('TERM')
        yield self.protocol.exited
        process = yield _run_node(
            reactor, self.process.node_dir, request, None,
        )
        self.process = process
        self.protocol = self.process.transport._protocol


    # XXX add stop / start / restart
    # ...maybe "reconfig" of some kind?


@inlineCallbacks
def create_client(reactor, request, temp_dir, introducer, flog_gatherer, name, web_port,
                  needed=2, happy=3, total=4):
    """
    Create a new storage server
    """
    from util import _create_node
    node_process = yield _create_node(
        reactor, request, temp_dir, introducer.furl, flog_gatherer,
        name, web_port, storage=False, needed=needed, happy=happy, total=total,
    )
    returnValue(
        Client(
            process=node_process,
            protocol=node_process.transport._protocol,
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
        validator=attr.validators.provides(IProcessProtocol)
    )
    furl = attr.ib()


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
    config = (
        '[node]\n'
        'nickname = introducer{port}\n'
        'web.port = {port}\n'
        'log_gatherer.furl = {log_furl}\n'
    ).format(
        port=port,
        log_furl=flog_gatherer.furl,
    )

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

    # over-write the config file with our stuff
    with open(join(intro_dir, 'tahoe.cfg'), 'w') as f:
        f.write(config)

    # on windows, "tahoe start" means: run forever in the foreground,
    # but on linux it means daemonize. "tahoe run" is consistent
    # between platforms.
    protocol = _MagicTextProtocol('introducer running')
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
    furl = open(furl_fname, 'r').read()

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
    Storage Servers.
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



# XXX THINK can we tie a whole *grid* to a single request? (I think
# that's all that makes sense)
@inlineCallbacks
def create_grid(reactor, request, temp_dir, flog_gatherer, port_allocator):
    """
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


def create_port_allocator(start_port):
    """
    Returns a new port-allocator .. which is a zero-argument function
    that returns Deferreds that fire with new, sequential ports
    starting at `start_port` skipping any that already appear to have
    a listener.

    There can still be a race against other processes allocating ports
    -- between the time when we check the status of the port and when
    our subprocess starts up. This *could* be mitigated by instructing
    the OS to not randomly-allocate ports in some range, and then
    using that range here (explicitly, ourselves).

    NB once we're Python3-only this could be an async-generator
    """
    port = [start_port - 1]

    # import stays here to not interfere with reactor selection -- but
    # maybe this function should be arranged to be called once from a
    # fixture (with the reactor)?
    from twisted.internet import reactor

    class NothingProtocol(Protocol):
        """
        I do nothing.
        """

    def port_generator():
        print("Checking port {}".format(port))
        port[0] += 1
        ep = TCP4ServerEndpoint(reactor, port[0], interface="localhost")
        d = ep.listen(Factory.forProtocol(NothingProtocol))

        def good(listening_port):
            unlisten_d = maybeDeferred(listening_port.stopListening)
            def return_port(_):
                return port[0]
            unlisten_d.addBoth(return_port)
            return unlisten_d

        def try_again(fail):
            return port_generator()

        d.addCallbacks(good, try_again)
        return d
    return port_generator
