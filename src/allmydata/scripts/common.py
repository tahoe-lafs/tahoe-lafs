
import os, sys, urllib
import codecs
from twisted.python import usage
from allmydata.util.stringutils import unicode_to_url, quote_output
from allmydata.util.assertutil import precondition

class BaseOptions:
    # unit tests can override these to point at StringIO instances
    stdin = sys.stdin
    stdout = sys.stdout
    stderr = sys.stderr

    optFlags = [
        ["quiet", "q", "Operate silently."],
        ["version", "V", "Display version numbers and exit."],
        ["version-and-path", None, "Display version numbers and paths to their locations and exit."],
        ]

    def opt_version(self):
        import allmydata
        print >>self.stdout, allmydata.get_package_versions_string()
        sys.exit(0)

    def opt_version_and_path(self):
        import allmydata
        print >>self.stdout, allmydata.get_package_versions_string(show_paths=True)
        sys.exit(0)


class BasedirMixin:
    optFlags = [
        ["multiple", "m", "allow multiple basedirs to be specified at once"],
        ]

    def postOptions(self):
        if not self.basedirs:
            raise usage.UsageError("<basedir> parameter is required")
        if self['basedir']:
            del self['basedir']
        self['basedirs'] = [os.path.abspath(os.path.expanduser(b)) for b in self.basedirs]

    def parseArgs(self, *args):
        self.basedirs = []
        if self['basedir']:
            precondition(isinstance(self['basedir'], (str, unicode)), self['basedir'])
            self.basedirs.append(self['basedir'])
        if self['multiple']:
            precondition(not [x for x in args if not isinstance(x, (str, unicode))], args)
            self.basedirs.extend(args)
        else:
            if len(args) == 0 and not self.basedirs:
                if sys.platform == 'win32':
                    from allmydata.windows import registry
                    rbdp = registry.get_base_dir_path()
                    if rbdp:
                        precondition(isinstance(registry.get_base_dir_path(), (str, unicode)), registry.get_base_dir_path())
                        self.basedirs.append(rbdp)
                else:
                    precondition(isinstance(os.path.expanduser("~/.tahoe"), (str, unicode)), os.path.expanduser("~/.tahoe"))
                    self.basedirs.append(os.path.expanduser("~/.tahoe"))
            if len(args) > 0:
                precondition(isinstance(args[0], (str, unicode)), args[0])
                self.basedirs.append(args[0])
            if len(args) > 1:
                raise usage.UsageError("I wasn't expecting so many arguments")

class NoDefaultBasedirMixin(BasedirMixin):
    def parseArgs(self, *args):
        # create-client won't default to --basedir=~/.tahoe
        self.basedirs = []
        if self['basedir']:
            precondition(isinstance(self['basedir'], (str, unicode)), self['basedir'])
            self.basedirs.append(self['basedir'])
        if self['multiple']:
            precondition(not [x for x in args if not isinstance(x, (str, unicode))], args)
            self.basedirs.extend(args)
        else:
            if len(args) > 0:
                precondition(isinstance(args[0], (str, unicode)), args[0])
                self.basedirs.append(args[0])
            if len(args) > 1:
                raise usage.UsageError("I wasn't expecting so many arguments")
        if not self.basedirs:
            raise usage.UsageError("--basedir must be provided")

DEFAULT_ALIAS = u"tahoe"


def get_aliases(nodedir):
    from allmydata import uri
    aliases = {}
    aliasfile = os.path.join(nodedir, "private", "aliases")
    rootfile = os.path.join(nodedir, "private", "root_dir.cap")
    try:
        f = open(rootfile, "r")
        rootcap = f.read().strip()
        if rootcap:
            aliases[u"tahoe"] = uri.from_string_dirnode(rootcap).to_string()
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
            aliases[name] = uri.from_string_dirnode(cap).to_string()
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
            raise UnknownAliasError("No alias specified, and the default "
                                    "'tahoe' alias doesn't exist. To create "
                                    "it, use 'tahoe create-alias tahoe'.")
        return aliases[default], path
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
            raise UnknownAliasError("No alias specified, and the default "
                                    "'tahoe' alias doesn't exist. To create "
                                    "it, use 'tahoe create-alias tahoe'.")
        return aliases[default], path
    if alias not in aliases:
        raise UnknownAliasError("Unknown alias %s, please create it with 'tahoe add-alias' or 'tahoe create-alias'." %
                                quote_output(alias))
    return aliases[alias], path[colon+1:]

def escape_path(path):
    segments = path.split("/")
    return "/".join([urllib.quote(unicode_to_url(s)) for s in segments])
