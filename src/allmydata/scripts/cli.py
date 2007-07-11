
import os.path, sys
from twisted.python import usage

class VDriveOptions(usage.Options):
    optParameters = [
        ["vdrive", "d", "global",
         "which virtual drive to use: 'global' or 'private'"],

        ["server", "s", "http://tahoebs1.allmydata.com:8011/",
         "which vdrive server to use, a URL like http://example.com/"],
        ]

class ListOptions(VDriveOptions):
    def parseArgs(self, vdrive_filename=""):
        self['vdrive_filename'] = vdrive_filename

class GetOptions(VDriveOptions):
    def parseArgs(self, vdrive_filename, local_filename="-"):
        self['vdrive_filename'] = vdrive_filename
        self['local_filename'] = local_filename

    def getSynopsis(self):
        return "%s get VDRIVE_FILE LOCAL_FILE" % (os.path.basename(sys.argv[0]),)

    longdesc = """Retrieve a file from the virtual drive and write it to the
    local disk. If LOCAL_FILE is omitted or '-', the contents of the file
    will be written to stdout."""


subCommands = [
    ["ls", None, ListOptions, "List a directory"],
    ["get", None, GetOptions, "Retrieve a file from the virtual drive."],
    ]

def list(config, stdout, stderr):
    from allmydata.scripts import tahoe_ls
    rc = tahoe_ls.list(config['server'],
                       config['vdrive'],
                       config['vdrive_filename'])
    return rc

def get(config, stdout, stderr):
    from allmydata.scripts import tahoe_get
    vdrive_filename = config['vdrive_filename']
    local_filename = config['local_filename']
    rc = tahoe_get.get(config['server'],
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

dispatch = {
    "ls": list,
    "get": get,
    }

