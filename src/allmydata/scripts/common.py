
import os, sys, urllib
import codecs
from twisted.python import usage
from allmydata.util.assertutil import precondition
from allmydata.util.encodingutil import unicode_to_url, quote_output, argv_to_abspath
from allmydata.util.fileutil import abspath_expanduser_unicode


_default_nodedir = None
if sys.platform == 'win32':
    from allmydata.windows import registry
    path = registry.get_base_dir_path()
    if path:
        precondition(isinstance(path, unicode), path)
        _default_nodedir = abspath_expanduser_unicode(path)

if _default_nodedir is None:
    path = abspath_expanduser_unicode(u"~/.tahoe")
    precondition(isinstance(path, unicode), path)
    _default_nodedir = path

def get_default_nodedir():
    return _default_nodedir


class BaseOptions(usage.Options):
    def __init__(self):
        super(BaseOptions, self).__init__()
        self.command_name = os.path.basename(sys.argv[0])
        if self.command_name == 'trial':
            self.command_name = 'tahoe'

    # Only allow "tahoe --version", not e.g. "tahoe start --version"
    def opt_version(self):
        raise usage.UsageError("--version not allowed on subcommands")

class BasedirOptions(BaseOptions):
    default_nodedir = _default_nodedir

    optParameters = [
        ["basedir", "C", None, "Same as --node-directory (default %s)."
         % get_default_nodedir()],
    ]

    def parseArgs(self, basedir=None):
        if self.parent['node-directory'] and self['basedir']:
            raise usage.UsageError("The --node-directory (or -d) and --basedir (or -C) options cannot both be used.")
        if self.parent['node-directory'] and basedir:
            raise usage.UsageError("The --node-directory (or -d) option and a basedir argument cannot both be used.")
        if self['basedir'] and basedir:
            raise usage.UsageError("The --basedir (or -C) option and a basedir argument cannot both be used.")

        if basedir:
            b = argv_to_abspath(basedir)
        elif self['basedir']:
            b = argv_to_abspath(self['basedir'])
        elif self.parent['node-directory']:
            b = argv_to_abspath(self.parent['node-directory'])
        elif self.default_nodedir:
            b = self.default_nodedir
        else:
            raise usage.UsageError("No default basedir available, you must provide one with --node-directory, --basedir, or a basedir argument")
        self['basedir'] = b

    def postOptions(self):
        if not self['basedir']:
            raise usage.UsageError("A base directory for the node must be provided.")


DEFAULT_ALIAS = u"tahoe"


def get_aliases(nodedir):
    aliases = {}
    aliasfile = os.path.join(nodedir, "private", "aliases")
    rootfile = os.path.join(nodedir, "private", "root_dir.cap")
    try:
        f = open(rootfile, "r")
        rootcap = f.read().strip()
        if rootcap:
            aliases[DEFAULT_ALIAS] = rootcap
    except EnvironmentError:
        pass
    try:
        f = codecs.open(aliasfile, "r", "utf-8")
        for line in f.readlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            name, cap = line.split(u":", 1)
            # normalize it: remove http: prefix, urldecode
            cap = cap.strip().encode('utf-8')
            aliases[name] = cap
    except EnvironmentError:
        pass
    return aliases

class DefaultAliasMarker:
    pass

pretend_platform_uses_lettercolon = False # for tests
def platform_uses_lettercolon_drivename():
    if ("win32" in sys.platform.lower()
        or "cygwin" in sys.platform.lower()
        or pretend_platform_uses_lettercolon):
        return True
    return False


class TahoeError(Exception):
    def __init__(self, msg):
        Exception.__init__(self, msg)
        self.msg = msg

    def display(self, err):
        print >>err, self.msg


class UnknownAliasError(TahoeError):
    def __init__(self, msg):
        TahoeError.__init__(self, "error: " + msg)


def get_alias(aliases, path_unicode, default):
    """
    Transform u"work:path/filename" into (aliases[u"work"], u"path/filename".encode('utf-8')).
    If default=None, then an empty alias is indicated by returning
    DefaultAliasMarker. We special-case strings with a recognized cap URI
    prefix, to make it easy to access specific files/directories by their
    caps.
    If the transformed alias is either not found in aliases, or is blank
    and default is not found in aliases, an UnknownAliasError is
    raised.
    """
    precondition(isinstance(path_unicode, unicode), path_unicode)

    from allmydata import uri
    path = path_unicode.encode('utf-8').strip(" ")
    if uri.has_uri_prefix(path):
        # We used to require "URI:blah:./foo" in order to get a subpath,
        # stripping out the ":./" sequence. We still allow that for compatibility,
        # but now also allow just "URI:blah/foo".
        sep = path.find(":./")
        if sep != -1:
            return path[:sep], path[sep+3:]
        sep = path.find("/")
        if sep != -1:
            return path[:sep], path[sep+1:]
        return path, ""
    colon = path.find(":")
    if colon == -1:
        # no alias
        if default == None:
            return DefaultAliasMarker, path
        if default not in aliases:
            raise UnknownAliasError("No alias specified, and the default %s alias doesn't exist. "
                                    "To create it, use 'tahoe create-alias %s'."
                                    % (quote_output(default), quote_output(default, quotemarks=False)))
        return uri.from_string_dirnode(aliases[default]).to_string(), path
    if colon == 1 and default is None and platform_uses_lettercolon_drivename():
        # treat C:\why\must\windows\be\so\weird as a local path, not a tahoe
        # file in the "C:" alias
        return DefaultAliasMarker, path

    # decoding must succeed because path is valid UTF-8 and colon & space are ASCII
    alias = path[:colon].decode('utf-8')
    if u"/" in alias:
        # no alias, but there's a colon in a dirname/filename, like
        # "foo/bar:7"
        if default == None:
            return DefaultAliasMarker, path
        if default not in aliases:
            raise UnknownAliasError("No alias specified, and the default %s alias doesn't exist. "
                                    "To create it, use 'tahoe create-alias %s'."
                                    % (quote_output(default), quote_output(default, quotemarks=False)))
        return uri.from_string_dirnode(aliases[default]).to_string(), path
    if alias not in aliases:
        raise UnknownAliasError("Unknown alias %s, please create it with 'tahoe add-alias' or 'tahoe create-alias'." %
                                quote_output(alias))
    return uri.from_string_dirnode(aliases[alias]).to_string(), path[colon+1:]

def escape_path(path):
    segments = path.split("/")
    return "/".join([urllib.quote(unicode_to_url(s)) for s in segments])
