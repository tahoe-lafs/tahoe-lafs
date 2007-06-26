#! /usr/bin/env python

import os, subprocess, sys, signal, time
from twisted.python import usage

from twisted.python.procutils import which

def testtwistd(loc):
    try:
        return subprocess.call(["python", loc,], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except:
        return -1
    
twistd = None
if not twistd:
    for maybetwistd in which("twistd"):
        ret = testtwistd(maybetwistd)
        if ret == 0:
            twistd = maybetwistd
            break

if not twistd:
    for maybetwistd in which("twistd.py"):
        ret = testtwistd(maybetwistd)
        if ret == 0:
            twistd = maybetwistd
            break

if not twistd:
    maybetwistd = os.path.join(sys.prefix, 'Scripts', 'twistd')
    ret = testtwistd(maybetwistd)
    if ret == 0:
        twistd = maybetwistd

if not twistd:
    maybetwistd = os.path.join(sys.prefix, 'Scripts', 'twistd.py')
    ret = testtwistd(maybetwistd)
    if ret == 0:
        twistd = maybetwistd

if not twistd:
    print "Can't find twistd (it comes with Twisted).  Aborting."
    sys.exit(1)

class BasedirMixin:
    optFlags = [
        ["multiple", "m", "allow multiple basedirs to be specified at once"],
        ]

    def postOptions(self):
        if not self.basedirs:
            raise usage.UsageError("<basedir> parameter is required")
        if self['basedir']:
            del self['basedir']
        self['basedirs'] = [os.path.abspath(os.path.expanduser(b))
                            for b in self.basedirs]

    def parseArgs(self, *args):
        self.basedirs = []
        if self['basedir']:
            self.basedirs.append(self['basedir'])
        if self['multiple']:
            self.basedirs.extend(args)
        else:
            if len(args) == 0 and not self.basedirs:
                self.basedirs.append(".")
            if len(args) > 0:
                self.basedirs.append(args[0])
            if len(args) > 1:
                raise usage.UsageError("I wasn't expecting so many arguments")

class NoDefaultBasedirMixin(BasedirMixin):
    def parseArgs(self, *args):
        # create-client won't default to --basedir=.
        self.basedirs = []
        if self['basedir']:
            self.basedirs.append(self['basedir'])
        if self['multiple']:
            self.basedirs.extend(args)
        else:
            if len(args) > 0:
                self.basedirs.append(args[0])
            if len(args) > 1:
                raise usage.UsageError("I wasn't expecting so many arguments")
        if not self.basedirs:
            raise usage.UsageError("--basedir must be provided")

class StartOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to start the node in"],
        ]

class StopOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to stop the node in"],
        ]

class RestartOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to restart the node in"],
        ]

class CreateClientOptions(NoDefaultBasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to create the client in"],
        ]
    optFlags = [
        ["quiet", "q", "operate silently"],
        ]

class CreateIntroducerOptions(NoDefaultBasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to create the introducer in"],
        ]
    optFlags = [
        ["quiet", "q", "operate silently"],
        ]

class DumpOptions(usage.Options):
    optParameters = [
        ["filename", "f", None, "which file to dump"],
        ]

    def parseArgs(self, filename=None):
        if filename:
            self['filename'] = filename

    def postOptions(self):
        if not self['filename']:
            raise usage.UsageError("<filename> parameter is required")

class DumpRootDirnodeOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "the vdrive-server's base directory"],
        ]

class DumpDirnodeOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["uri", "u", None, "the URI of the dirnode to dump."],
        ["basedir", "C", None, "which directory to create the introducer in"],
        ]
    optFlags = [
        ["verbose", "v", "be extra noisy (show encrypted data)"],
        ]
    def parseArgs(self, *args):
        if len(args) == 1:
            self['uri'] = args[-1]
            args = args[:-1]
        BasedirMixin.parseArgs(self, *args)

    def postOptions(self):
        BasedirMixin.postOptions(self)
        if not self['uri']:
            raise usage.UsageError("<uri> parameter is required")

client_tac = """
# -*- python -*-

from allmydata import client
from twisted.application import service

c = client.Client()

application = service.Application("allmydata_client")
c.setServiceParent(application)
"""

introducer_tac = """
# -*- python -*-

from allmydata import introducer_and_vdrive
from twisted.application import service

c = introducer_and_vdrive.IntroducerAndVdrive()

application = service.Application("allmydata_introducer")
c.setServiceParent(application)
"""

class Options(usage.Options):
    synopsis = "Usage:  allmydata <command> [command options]"

    subCommands = [
        ["create-client", None, CreateClientOptions, "Create a client node."],
        ["create-introducer", None, CreateIntroducerOptions, "Create a introducer node."],
        ["start", None, StartOptions, "Start a node (of any type)."],
        ["stop", None, StopOptions, "Stop a node."],
        ["restart", None, RestartOptions, "Restart a node."],
        ["dump-uri-extension", None, DumpOptions,
         "Unpack and display the contents of a uri_extension file."],
        ["dump-root-dirnode", None, DumpRootDirnodeOptions,
         "Compute most of the URI for the vdrive server's root dirnode."],
        ["dump-dirnode", None, DumpDirnodeOptions,
         "Unpack and display the contents of a vdrive DirectoryNode."],
        ]

    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a command")

def runner(argv, run_by_human=True):
    config = Options()
    try:
        config.parseOptions(argv)
    except usage.error, e:
        if not run_by_human:
            raise
        print "%s:  %s" % (sys.argv[0], e)
        print
        c = getattr(config, 'subOptions', config)
        print str(c)
        return 1

    command = config.subCommand
    so = config.subOptions

    rc = 0
    if command == "create-client":
        for basedir in so.basedirs:
            rc = create_client(basedir, so) or rc
    elif command == "create-introducer":
        for basedir in so.basedirs:
            rc = create_introducer(basedir, so) or rc
    elif command == "start":
        for basedir in so.basedirs:
            rc = start(basedir, so) or rc
    elif command == "stop":
        for basedir in so.basedirs:
            rc = stop(basedir, so) or rc
    elif command == "restart":
        for basedir in so.basedirs:
            rc = stop(basedir, so) or rc
        if rc:
            print "not restarting"
            return rc
        for basedir in so.basedirs:
            rc = start(basedir, so) or rc
    elif command == "dump-uri-extension":
        rc = dump_uri_extension(so)
    elif command == "dump-root-dirnode":
        rc = dump_root_dirnode(so.basedirs[0], so)
    elif command == "dump-dirnode":
        rc = dump_directory_node(so.basedirs[0], so)
    return rc

def run():
    rc = runner(sys.argv[1:])
    sys.exit(rc)

def create_client(basedir, config):
    if os.path.exists(basedir):
        if os.listdir(basedir):
            print "The base directory already exists: %s" % basedir
            print "To avoid clobbering anything, I am going to quit now"
            print "Please use a different directory, or delete this one"
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    f = open(os.path.join(basedir, "client.tac"), "w")
    f.write(client_tac)
    f.close()
    if not config['quiet']:
        print "client created in %s" % basedir
        print " please copy introducer.furl and vdrive.furl into the directory"

def create_introducer(basedir, config):
    if os.path.exists(basedir):
        if os.listdir(basedir):
            print "The base directory already exists: %s" % basedir
            print "To avoid clobbering anything, I am going to quit now"
            print "Please use a different directory, or delete this one"
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    f = open(os.path.join(basedir, "introducer.tac"), "w")
    f.write(introducer_tac)
    f.close()
    if not config['quiet']:
        print "introducer created in %s" % basedir

def start(basedir, config):
    print "STARTING", basedir
    if os.path.exists(os.path.join(basedir, "client.tac")):
        tac = "client.tac"
        type = "client"
    elif os.path.exists(os.path.join(basedir, "introducer.tac")):
        tac = "introducer.tac"
        type = "introducer"
    else:
        print "%s does not look like a node directory" % basedir
        if not os.path.isdir(basedir):
            print " in fact, it doesn't look like a directory at all!"
        sys.exit(1)
    rc = subprocess.call(["python", twistd, "-y", tac,], cwd=basedir)
    if rc == 0:
        print "%s node probably started" % type
        return 0
    else:
        print "%s node probably not started" % type
        return 1

def stop(basedir, config):
    print "STOPPING", basedir
    pidfile = os.path.join(basedir, "twistd.pid")
    if not os.path.exists(pidfile):
        print "%s does not look like a running node directory (no twistd.pid)" % basedir
        return 1
    pid = open(pidfile, "r").read()
    pid = int(pid)

    timer = 0
    os.kill(pid, signal.SIGTERM)
    time.sleep(0.1)
    while timer < 5:
        # poll once per second until twistd.pid goes away, up to 5 seconds
        try:
            os.kill(pid, 0)
        except OSError:
            print "process %d is dead" % pid
            return
        timer += 1
        time.sleep(1)
    print "never saw process go away"
    return 1

def dump_uri_extension(config):
    from allmydata import uri

    filename = config['filename']
    unpacked = uri.unpack_extension_readable(open(filename,"rb").read())
    keys1 = ("size", "num_segments", "segment_size",
             "needed_shares", "total_shares")
    keys2 = ("codec_name", "codec_params", "tail_codec_params")
    keys3 = ("plaintext_hash", "plaintext_root_hash",
             "crypttext_hash", "crypttext_root_hash",
             "share_root_hash")
    for k in keys1:
        if k in unpacked:
            print "%19s: %s" % (k, unpacked[k])
    print
    for k in keys2:
        if k in unpacked:
            print "%19s: %s" % (k, unpacked[k])
    print
    for k in keys3:
        if k in unpacked:
            print "%19s: %s" % (k, unpacked[k])

    leftover = set(unpacked.keys()) - set(keys1 + keys2 + keys3)
    if leftover:
        print
        for k in sorted(leftover):
            print "%s: %s" % (k, unpacked[k])

    print
    return 0

def dump_root_dirnode(basedir, config, output=sys.stdout):
    from allmydata import uri

    root_dirnode_file = os.path.join(basedir, "vdrive", "root")
    try:
        f = open(root_dirnode_file, "rb")
        key = f.read()
        rooturi = uri.pack_dirnode_uri("fakeFURL", key)
        print >>output, rooturi
        return 0
    except EnvironmentError:
        print >>output,  "unable to read root dirnode file from %s" % \
              root_dirnode_file
        return 1

def dump_directory_node(basedir, config, f=sys.stdout):
    from allmydata import filetable, vdrive, uri
    from allmydata.util import hashutil, idlib
    dir_uri = config['uri']
    verbose = config['verbose']

    furl, key = uri.unpack_dirnode_uri(dir_uri)
    if uri.is_mutable_dirnode_uri(dir_uri):
        wk, we, rk, index = hashutil.generate_dirnode_keys_from_writekey(key)
    else:
        wk, we, rk, index = hashutil.generate_dirnode_keys_from_readkey(key)

    filename = os.path.join(basedir, "vdrive", idlib.b2a(index))

    print >>f
    print >>f, "dirnode uri: %s" % dir_uri
    print >>f, "filename : %s" % filename
    print >>f, "index        : %s" % idlib.b2a(index)
    if wk:
        print >>f, "writekey     : %s" % idlib.b2a(wk)
        print >>f, "write_enabler: %s" % idlib.b2a(we)
    else:
        print >>f, "writekey     : None"
        print >>f, "write_enabler: None"
    print >>f, "readkey      : %s" % idlib.b2a(rk)

    print >>f

    vds = filetable.VirtualDriveServer(os.path.join(basedir, "vdrive"), False)
    data = vds._read_from_file(index)
    if we:
        if we != data[0]:
            print >>f, "ERROR: write_enabler does not match"

    for (H_key, E_key, E_write, E_read) in data[1]:
        if verbose:
            print >>f, " H_key %s" % idlib.b2a(H_key)
            print >>f, " E_key %s" % idlib.b2a(E_key)
            print >>f, " E_write %s" % idlib.b2a(E_write)
            print >>f, " E_read %s" % idlib.b2a(E_read)
        key = vdrive.decrypt(rk, E_key)
        print >>f, " key %s" % key
        if hashutil.dir_name_hash(rk, key) != H_key:
            print >>f, "  ERROR: H_key does not match"
        if wk and E_write:
            if len(E_write) < 14:
                print >>f, "  ERROR: write data is short:", idlib.b2a(E_write)
            write = vdrive.decrypt(wk, E_write)
            print >>f, "   write: %s" % write
        read = vdrive.decrypt(rk, E_read)
        print >>f, "   read: %s" % read
        print >>f

    return 0
