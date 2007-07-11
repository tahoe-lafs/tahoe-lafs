
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


subCommands = [
    ["ls", None, ListOptions, "List a directory"],
    ["get", None, GetOptions, "Retrieve a file from the virtual drive"],
    ]

def list(config, stdout, stderr):
    from allmydata.scripts import tahoe_ls
    rc = tahoe_ls.list(config['server'],
                       config['vdrive'],
                       config['vdrive_filename'])
    return rc

def get(config, stdout, stderr):
    from allmydata.scripts import tahoe_get
    rc = tahoe_get.get(config['server'],
                       config['vdrive'],
                       config['vdrive_filename'])
    return rc

dispatch = {
    "ls": list,
    "get": get,
    }

