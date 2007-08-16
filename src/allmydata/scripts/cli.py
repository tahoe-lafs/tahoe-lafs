
import os.path, sys
from twisted.python import usage
from allmydata.scripts.common import BaseOptions

class VDriveOptions(BaseOptions, usage.Options):
    optParameters = [
        ["vdrive", "d", "global",
         "which virtual drive to use: 'global' or 'private'"],

        ["node-url", "u", None,
         "URL of the tahoe node to use, a URL like \"http://127.0.0.1:8888\""],
        ]

class ListOptions(VDriveOptions):
    def parseArgs(self, vdrive_filename=""):
        self['vdrive_filename'] = vdrive_filename

    longdesc = """List the contents of some portion of the virtual drive."""

class GetOptions(VDriveOptions):
    def parseArgs(self, vdrive_filename, local_filename="-"):
        self['vdrive_filename'] = vdrive_filename
        self['local_filename'] = local_filename

    def getSynopsis(self):
        return "%s get VDRIVE_FILE LOCAL_FILE" % (os.path.basename(sys.argv[0]),)

    longdesc = """Retrieve a file from the virtual drive and write it to the
    local filesystem. If LOCAL_FILE is omitted or '-', the contents of the file
    will be written to stdout."""

class PutOptions(VDriveOptions):
    def parseArgs(self, local_filename, vdrive_filename):
        self['vdrive_filename'] = vdrive_filename
        self['local_filename'] = local_filename

    def getSynopsis(self):
        return "%s put LOCAL_FILEVDRI VE_FILE" % (os.path.basename(sys.argv[0]),)

    longdesc = """Put a file into the virtual drive (copying the file's
    contents from the local filesystem). If LOCAL_FILE is omitted or '-', the
    contents of the file will be read from stdin."""



subCommands = [
    ["ls", None, ListOptions, "List a directory"],
    ["get", None, GetOptions, "Retrieve a file from the virtual drive."],
    ["put", None, PutOptions, "Upload a file into the virtual drive."],
    ]

def list(config, stdout, stderr):
    from allmydata.scripts import tahoe_ls
    rc = tahoe_ls.list(config['node-url'],
                       config['vdrive'],
                       config['vdrive_filename'])
    return rc

def get(config, stdout, stderr):
    from allmydata.scripts import tahoe_get
    vdrive_filename = config['vdrive_filename']
    local_filename = config['local_filename']
    rc = tahoe_get.get(config['node-url'],
                       config['vdrive'],
                       vdrive_filename,
                       local_filename)
    if rc == 0:
        if local_filename is None or local_filename == "-":
            # be quiet, since the file being written to stdout should be
            # proof enough that it worked, unless the user is unlucky
            # enough to have picked an empty file
            pass
        else:
            print >>stderr, "%s retrieved and written to %s" % \
                  (vdrive_filename, local_filename)
    return rc

def put(config, stdout, stderr):
    from allmydata.scripts import tahoe_put
    vdrive_filename = config['vdrive_filename']
    local_filename = config['local_filename']
    if config['quiet']:
        verbosity = 0
    else:
        verbosity = 2
    rc = tahoe_put.put(config['node-url'],
                       config['vdrive'],
                       vdrive_filename,
                       local_filename,
                       verbosity)
    return rc

dispatch = {
    "ls": list,
    "get": get,
    "put": put,
    }

