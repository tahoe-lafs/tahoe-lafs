
import os.path, re, sys
from twisted.python import usage
from allmydata.scripts.common import BaseOptions

NODEURL_RE=re.compile("http://([^:]*)(:([1-9][0-9]*))?")

class VDriveOptions(BaseOptions, usage.Options):
    optParameters = [
        ["node-directory", "d", "~/.tahoe",
         "Look here to find out which Tahoe node should be used for all "
         "operations. The directory should either contain a full Tahoe node, "
         "or a file named node.url which points to some other Tahoe node. "
         "It should also contain a file named root_dir.cap which contains "
         "the root dirnode URI that should be used."
         ],
        ["node-url", "u", None,
         "URL of the tahoe node to use, a URL like \"http://127.0.0.1:8123\". "
         "This overrides the URL found in the --node-directory ."],
        ["dir-uri", "r", "root",
         "Which dirnode URI should be used as a root directory.  The "
         "string 'root' is special, and means we should use the "
         "directory found in the 'root_dir.cap' file in the 'private' "
         "subdirectory of the --node-directory ."],
        ]

    def postOptions(self):
        # compute a node-url from the existing options, put in self['node-url']
        if self['node-directory']:
            self['node-directory'] = os.path.expanduser(self['node-directory'])
        if self['node-url']:
            if (not isinstance(self['node-url'], basestring)
                or not NODEURL_RE.match(self['node-url'])):
                msg = ("--node-url is required to be a string and look like "
                       "\"http://HOSTNAMEORADDR:PORT\", not: %r" %
                       (self['node-url'],))
                raise usage.UsageError(msg)
        else:
            node_url_file = os.path.join(self['node-directory'], "node.url")
            self['node-url'] = open(node_url_file, "r").read().strip()

        rootdircap = None
        if self['dir-uri'] == 'root':
            uri_file = os.path.join(self['node-directory'], 'private', "root_dir.cap")
            try:
                rootdircap = open(uri_file, "r").read().strip()
            except EnvironmentError, le:
                raise usage.UsageError("\n"
                                       "If --dir-uri is absent or is 'root', then the node directory's 'private'\n"
                                       "subdirectory is required to contain a file named 'root_dir.cap' which must\n"
                                       "contain a dir cap, but when we tried to open that file we got:\n"
                                       "'%s'." % (le,))
        else:
            rootdircap = self['dir-uri']
        from allmydata import uri
        try:
            parsed = uri.NewDirectoryURI.init_from_human_encoding(rootdircap)
        except:
            try:
                parsed = uri.ReadonlyNewDirectoryURI.init_from_human_encoding(rootdircap)
            except:
                if self['dir-uri'] == 'root':
                    raise usage.UsageError("\n"
                                           "If --dir-uri is absent or is 'root', then the node directory's 'private'\n"
                                           "subdirectory's 'root_dir.cap' is required to contain a dir cap, but we found\n"
                                           "'%s'." % (rootdircap,))
                else:
                    raise usage.UsageError("--dir-uri must be a dir cap (or \"root\"), but we got '%s'." % (self['dir-uri'],))

        self['dir-uri'] = parsed.to_string()

class ListOptions(VDriveOptions):
    def parseArgs(self, vdrive_pathname=""):
        self['vdrive_pathname'] = vdrive_pathname

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
        self['local_filename'] = local_filename
        self['vdrive_filename'] = vdrive_filename

    def getSynopsis(self):
        return "%s put LOCAL_FILE VDRIVE_FILE" % (os.path.basename(sys.argv[0]),)

    longdesc = """Put a file into the virtual drive (copying the file's
    contents from the local filesystem). LOCAL_FILE is required to be a
    local file (it can't be stdin)."""

class RmOptions(VDriveOptions):
    def parseArgs(self, vdrive_pathname):
        self['vdrive_pathname'] = vdrive_pathname

    def getSynopsis(self):
        return "%s rm VE_FILE" % (os.path.basename(sys.argv[0]),)

class MvOptions(VDriveOptions):
    def parseArgs(self, frompath, topath):
        self['from'] = frompath
        self['to'] = topath

    def getSynopsis(self):
        return "%s mv FROM TO" % (os.path.basename(sys.argv[0]),)


subCommands = [
    ["ls", None, ListOptions, "List a directory"],
    ["get", None, GetOptions, "Retrieve a file from the virtual drive."],
    ["put", None, PutOptions, "Upload a file into the virtual drive."],
    ["rm", None, RmOptions, "Unlink a file or directory in the virtual drive."],
    ["mv", None, MvOptions, "Move a file within the virtual drive."],
    ]

def list(config, stdout, stderr):
    from allmydata.scripts import tahoe_ls
    rc = tahoe_ls.list(config['node-url'],
                       config['dir-uri'],
                       config['vdrive_pathname'],
                       stdout, stderr)
    return rc

def get(config, stdout, stderr):
    from allmydata.scripts import tahoe_get
    vdrive_filename = config['vdrive_filename']
    local_filename = config['local_filename']
    rc = tahoe_get.get(config['node-url'],
                       config['dir-uri'],
                       vdrive_filename,
                       local_filename,
                       stdout, stderr)
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
                       config['dir-uri'],
                       local_filename,
                       vdrive_filename,
                       verbosity,
                       stdout, stderr)
    return rc

def rm(config, stdout, stderr):
    from allmydata.scripts import tahoe_rm
    vdrive_pathname = config['vdrive_pathname']
    if config['quiet']:
        verbosity = 0
    else:
        verbosity = 2
    rc = tahoe_rm.rm(config['node-url'],
                     config['dir-uri'],
                     vdrive_pathname,
                     verbosity,
                     stdout, stderr)
    return rc

def mv(config, stdout, stderr):
    from allmydata.scripts import tahoe_mv
    frompath = config['from']
    topath = config['to']
    rc = tahoe_mv.mv(config['node-url'],
                     config['dir-uri'],
                     frompath,
                     topath,
                     stdout, stderr)
    return rc

dispatch = {
    "ls": list,
    "get": get,
    "put": put,
    "rm": rm,
    "mv": mv,
    }

