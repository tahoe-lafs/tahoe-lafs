from os import mkdir, listdir, environ
from os.path import join, exists
from tempfile import mkdtemp, mktemp

from twisted.python.procutils import which
from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)
from twisted.internet.task import (
    deferLater,
)
from twisted.internet.interfaces import (
    IProcessTransport,
    IProcessProtocol,
    IProtocol,
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
    await_client_ready,
    TahoeProcess,
)

import attr
import pytest_twisted


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

    # XXX needs a restart() probably .. or at least a stop() and
    # start()


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


_introducer_num = 0


@inlineCallbacks
def create_introducer(reactor, request, temp_dir, flog_gatherer):
    """
    Run a new Introducer and return an Introducer instance.
    """
    global _introducer_num
    config = (
        '[node]\n'
        'nickname = introducer{num}\n'
        'web.port = {port}\n'
        'log_gatherer.furl = {log_furl}\n'
    ).format(
        num=_introducer_num,
        log_furl=flog_gatherer.furl,
        port=4560 + _introducer_num,
    )
    _introducer_num += 1

    intro_dir = join(temp_dir, 'introducer{}'.format(_introducer_num))
    print("making introducer", intro_dir, _introducer_num)

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

    introducer = attr.ib(default=None)
    flog_gatherer = attr.ib(default=None)
    storage_servers = attr.ib(factory=list)

    @storage_servers.validator
    def check(self, attribute, value):
        for server in value:
            if not isinstance(server, StorageServer):
                raise ValueError(
                    "storage_servers must be StorageServer"
                )
