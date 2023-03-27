# coding: utf-8

"""
Ported to Python 3.
"""

from typing import Union, Optional

import os, sys, textwrap
import codecs
from os.path import join
import urllib.parse

from yaml import (
    safe_dump,
)

from twisted.python import usage

from allmydata.util.assertutil import precondition
from allmydata.util.encodingutil import quote_output, \
    quote_local_unicode_path, argv_to_abspath
from allmydata.scripts.default_nodedir import _default_nodedir
from .types_ import Parameters


def get_default_nodedir():
    return _default_nodedir

def wrap_paragraphs(text, width):
    # like textwrap.wrap(), but preserve paragraphs (delimited by double
    # newlines) and leading whitespace, and remove internal whitespace.
    text = textwrap.dedent(text)
    if text.startswith("\n"):
        text = text[1:]
    return "\n\n".join([textwrap.fill(paragraph, width=width)
                        for paragraph in text.split("\n\n")])

class BaseOptions(usage.Options):
    def __init__(self):
        super(BaseOptions, self).__init__()
        self.command_name = os.path.basename(sys.argv[0])

    # Only allow "tahoe --version", not e.g. "tahoe <cmd> --version"
    def opt_version(self):
        raise usage.UsageError("--version not allowed on subcommands")

    description : Optional[str] = None
    description_unwrapped = None  # type: Optional[str]

    def __str__(self):
        width = int(os.environ.get('COLUMNS', '80'))
        s = (self.getSynopsis() + '\n' +
             "(use 'tahoe --help' to view global options)\n" +
             '\n' +
             self.getUsage())
        if self.description:
            s += '\n' + wrap_paragraphs(self.description, width) + '\n'
        if self.description_unwrapped:
            du = textwrap.dedent(self.description_unwrapped)
            if du.startswith("\n"):
                du = du[1:]
            s += '\n' + du + '\n'
        return s

class BasedirOptions(BaseOptions):
    default_nodedir = _default_nodedir

    optParameters : Parameters = [
        ["basedir", "C", None, "Specify which Tahoe base directory should be used. [default: %s]"
         % quote_local_unicode_path(_default_nodedir)],
    ]

    def parseArgs(self, basedir=None):
        # This finds the node-directory option correctly even if we are in a subcommand.
        root = self.parent
        while root.parent is not None:
            root = root.parent

        if root['node-directory'] and self['basedir']:
            raise usage.UsageError("The --node-directory (or -d) and --basedir (or -C) options cannot both be used.")
        if root['node-directory'] and basedir:
            raise usage.UsageError("The --node-directory (or -d) option and a basedir argument cannot both be used.")
        if self['basedir'] and basedir:
            raise usage.UsageError("The --basedir (or -C) option and a basedir argument cannot both be used.")

        if basedir:
            b = argv_to_abspath(basedir)
        elif self['basedir']:
            b = argv_to_abspath(self['basedir'])
        elif root['node-directory']:
            b = argv_to_abspath(root['node-directory'])
        elif self.default_nodedir:
            b = self.default_nodedir
        else:
            raise usage.UsageError("No default basedir available, you must provide one with --node-directory, --basedir, or a basedir argument")
        self['basedir'] = b
        self['node-directory'] = b

    def postOptions(self):
        if not self['basedir']:
            raise usage.UsageError("A base directory for the node must be provided.")

class NoDefaultBasedirOptions(BasedirOptions):
    default_nodedir = None

    optParameters = [
        ["basedir", "C", None, "Specify which Tahoe base directory should be used."],
    ]  # type: Parameters

    # This is overridden in order to ensure we get a "Wrong number of arguments."
    # error when more than one argument is given.
    def parseArgs(self, basedir=None):
        BasedirOptions.parseArgs(self, basedir)

    def getSynopsis(self):
        return "Usage:  %s [global-options] %s [options] NODEDIR" % (self.command_name, self.subcommand_name)


DEFAULT_ALIAS = u"tahoe"


def write_introducer(basedir, petname, furl):
    """
    Overwrite the node's ``introducers.yaml`` with a file containing the given
    introducer information.
    """
    if isinstance(furl, bytes):
        furl = furl.decode("utf-8")
    private = basedir.child(b"private")
    private.makedirs(ignoreExistingDirectory=True)
    private.child(b"introducers.yaml").setContent(
        safe_dump({
            "introducers": {
                petname: {
                    "furl": furl,
                },
            },
        }).encode("ascii"),
    )


def get_introducer_furl(nodedir, config):
    """
    :return: the introducer FURL for the given node (no matter if it's
        a client-type node or an introducer itself)
    """
    for petname, (furl, cache) in config.get_introducer_configuration().items():
        return furl

    # We have no configured introducers.  Maybe this is running *on* the
    # introducer?  Let's guess, sure why not.
    try:
        with open(join(nodedir, "private", "introducer.furl"), "r") as f:
            return f.read().strip()
    except IOError:
        raise Exception(
            "Can't find introducer FURL in tahoe.cfg nor "
            "{}/private/introducer.furl".format(nodedir)
        )


def get_aliases(nodedir):
    aliases = {}
    aliasfile = os.path.join(nodedir, "private", "aliases")
    rootfile = os.path.join(nodedir, "private", "root_dir.cap")
    try:
        with open(rootfile, "r") as f:
            rootcap = f.read().strip()
            if rootcap:
                aliases[DEFAULT_ALIAS] = rootcap
    except EnvironmentError:
        pass
    try:
        with codecs.open(aliasfile, "r", "utf-8") as f:
            for line in f:
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

class DefaultAliasMarker(object):
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
        print(self.msg, file=err)


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
    precondition(isinstance(path_unicode, str), path_unicode)

    from allmydata import uri
    path = path_unicode.encode('utf-8').strip(b" ")
    if uri.has_uri_prefix(path):
        # We used to require "URI:blah:./foo" in order to get a subpath,
        # stripping out the ":./" sequence. We still allow that for compatibility,
        # but now also allow just "URI:blah/foo".
        sep = path.find(b":./")
        if sep != -1:
            return path[:sep], path[sep+3:]
        sep = path.find(b"/")
        if sep != -1:
            return path[:sep], path[sep+1:]
        return path, b""
    colon = path.find(b":")
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

def escape_path(path: Union[str, bytes]) -> str:
    """
    Return path quoted to US-ASCII, valid URL characters.

    >>> path = u'/føö/bar/☃'
    >>> escaped = escape_path(path)
    >>> escaped
    u'/f%C3%B8%C3%B6/bar/%E2%98%83'
    """
    if isinstance(path, str):
        path = path.encode("utf-8")
    segments = path.split(b"/")
    result = str(
        b"/".join([
            urllib.parse.quote(s).encode("ascii") for s in segments
        ]),
        "ascii"
    )
    return result
