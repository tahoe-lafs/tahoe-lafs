
import os.path
import urllib
import simplejson
from collections import defaultdict
from cStringIO import StringIO
from twisted.python.failure import Failure
from allmydata.scripts.common import get_alias, escape_path, \
                                     DefaultAliasMarker, TahoeError
from allmydata.scripts.common_http import do_http, HTTPError
from allmydata import uri
from allmydata.util import fileutil
from allmydata.util.fileutil import abspath_expanduser_unicode, precondition_abspath
from allmydata.util.encodingutil import unicode_to_url, listdir_unicode, quote_output, \
    quote_local_unicode_path, to_str
from allmydata.util.assertutil import precondition, _assert


class MissingSourceError(TahoeError):
    def __init__(self, name, quotefn=quote_output):
        TahoeError.__init__(self, "No such file or directory %s" % quotefn(name))

class FilenameWithTrailingSlashError(TahoeError):
    def __init__(self, name, quotefn=quote_output):
        TahoeError.__init__(self, "source '%s' is not a directory, but ends with a slash" % quotefn(name))

class WeirdSourceError(TahoeError):
    def __init__(self, absname):
        quoted = quote_local_unicode_path(absname)
        TahoeError.__init__(self, "source '%s' is neither a file nor a directory, I can't handle it" % quoted)

def GET_to_file(url):
    resp = do_http("GET", url)
    if resp.status == 200:
        return resp
    raise HTTPError("Error during GET", resp)

def GET_to_string(url):
    f = GET_to_file(url)
    return f.read()

def PUT(url, data):
    resp = do_http("PUT", url, data)
    if resp.status in (200, 201):
        return resp.read()
    raise HTTPError("Error during PUT", resp)

def POST(url, data):
    resp = do_http("POST", url, data)
    if resp.status in (200, 201):
        return resp.read()
    raise HTTPError("Error during POST", resp)

def mkdir(targeturl):
    url = targeturl + "?t=mkdir"
    resp = do_http("POST", url)
    if resp.status in (200, 201):
        return resp.read().strip()
    raise HTTPError("Error during mkdir", resp)

def make_tahoe_subdirectory(nodeurl, parent_writecap, name):
    url = nodeurl + "/".join(["uri",
                              urllib.quote(parent_writecap),
                              urllib.quote(unicode_to_url(name)),
                              ]) + "?t=mkdir"
    resp = do_http("POST", url)
    if resp.status in (200, 201):
        return resp.read().strip()
    raise HTTPError("Error during mkdir", resp)


class LocalFileSource:
    def __init__(self, pathname, basename):
        precondition_abspath(pathname)
        self.pathname = pathname
        self._basename = basename

    def basename(self):
        return self._basename

    def need_to_copy_bytes(self):
        return True

    def open(self, caps_only):
        return open(self.pathname, "rb")

class LocalFileTarget:
    def __init__(self, pathname):
        precondition_abspath(pathname)
        self.pathname = pathname

    def put_file(self, inf):
        fileutil.put_file(self.pathname, inf)

class LocalMissingTarget:
    def __init__(self, pathname):
        precondition_abspath(pathname)
        self.pathname = pathname

    def put_file(self, inf):
        fileutil.put_file(self.pathname, inf)

class LocalDirectorySource:
    def __init__(self, progressfunc, pathname, basename):
        precondition_abspath(pathname)

        self.progressfunc = progressfunc
        self.pathname = pathname
        self.children = None
        self._basename = basename

    def basename(self):
        return self._basename

    def populate(self, recurse):
        if self.children is not None:
            return
        self.children = {}
        children = listdir_unicode(self.pathname)
        for i,n in enumerate(children):
            self.progressfunc("examining %d of %d" % (i+1, len(children)))
            pn = os.path.join(self.pathname, n)
            if os.path.isdir(pn):
                child = LocalDirectorySource(self.progressfunc, pn, n)
                self.children[n] = child
                if recurse:
                    child.populate(recurse=True)
            elif os.path.isfile(pn):
                self.children[n] = LocalFileSource(pn, n)
            else:
                # Could be dangling symlink; probably not copy-able.
                # TODO: output a warning
                pass

class LocalDirectoryTarget:
    def __init__(self, progressfunc, pathname):
        precondition_abspath(pathname)

        self.progressfunc = progressfunc
        self.pathname = pathname
        self.children = None

    def populate(self, recurse):
        if self.children is not None:
            return
        self.children = {}
        children = listdir_unicode(self.pathname)
        for i,n in enumerate(children):
            self.progressfunc("examining %d of %d" % (i+1, len(children)))
            pn = os.path.join(self.pathname, n)
            if os.path.isdir(pn):
                child = LocalDirectoryTarget(self.progressfunc, pn)
                self.children[n] = child
                if recurse:
                    child.populate(recurse=True)
            else:
                assert os.path.isfile(pn)
                self.children[n] = LocalFileTarget(pn)

    def get_child_target(self, name):
        precondition(isinstance(name, unicode), name)
        precondition(len(name), name) # don't want ""
        if self.children is None:
            self.populate(recurse=False)
        if name in self.children:
            return self.children[name]
        pathname = os.path.join(self.pathname, name)
        os.makedirs(pathname)
        child = LocalDirectoryTarget(self.progressfunc, pathname)
        self.children[name] = child
        return child

    def put_file(self, name, inf):
        precondition(isinstance(name, unicode), name)
        pathname = os.path.join(self.pathname, name)
        fileutil.put_file(pathname, inf)

    def set_children(self):
        pass


class TahoeFileSource:
    def __init__(self, nodeurl, mutable, writecap, readcap, basename):
        self.nodeurl = nodeurl
        self.mutable = mutable
        self.writecap = writecap
        self.readcap = readcap
        self._basename = basename # unicode, or None for raw filecaps

    def basename(self):
        return self._basename

    def need_to_copy_bytes(self):
        if self.mutable:
            return True
        return False

    def open(self, caps_only):
        if caps_only:
            return StringIO(self.readcap)
        url = self.nodeurl + "uri/" + urllib.quote(self.readcap)
        return GET_to_file(url)

    def bestcap(self):
        return self.writecap or self.readcap

class TahoeFileTarget:
    def __init__(self, nodeurl, mutable, writecap, readcap, url):
        self.nodeurl = nodeurl
        self.mutable = mutable
        self.writecap = writecap
        self.readcap = readcap
        self.url = url

    def put_file(self, inf):
        # We want to replace this object in-place.
        assert self.url
        # our do_http() call currently requires a string or a filehandle with
        # a real .seek
        if not hasattr(inf, "seek"):
            inf = inf.read()
        PUT(self.url, inf)
        # TODO: this always creates immutable files. We might want an option
        # to always create mutable files, or to copy mutable files into new
        # mutable files. ticket #835

class TahoeDirectorySource:
    def __init__(self, nodeurl, cache, progressfunc, basename):
        self.nodeurl = nodeurl
        self.cache = cache
        self.progressfunc = progressfunc
        self._basename = basename # unicode, or None for raw dircaps

    def basename(self):
        return self._basename

    def init_from_grid(self, writecap, readcap):
        self.writecap = writecap
        self.readcap = readcap
        bestcap = writecap or readcap
        url = self.nodeurl + "uri/%s" % urllib.quote(bestcap)
        resp = do_http("GET", url + "?t=json")
        if resp.status != 200:
            raise HTTPError("Error examining source directory", resp)
        parsed = simplejson.loads(resp.read())
        nodetype, d = parsed
        assert nodetype == "dirnode"
        self.mutable = d.get("mutable", False) # older nodes don't provide it
        self.children_d = dict( [(unicode(name),value)
                                 for (name,value)
                                 in d["children"].iteritems()] )
        self.children = None

    def init_from_parsed(self, parsed):
        nodetype, d = parsed
        self.writecap = to_str(d.get("rw_uri"))
        self.readcap = to_str(d.get("ro_uri"))
        self.mutable = d.get("mutable", False) # older nodes don't provide it
        self.children_d = dict( [(unicode(name),value)
                                 for (name,value)
                                 in d["children"].iteritems()] )
        self.children = None

    def populate(self, recurse):
        if self.children is not None:
            return
        self.children = {}
        for i,(name, data) in enumerate(self.children_d.items()):
            self.progressfunc("examining %d of %d" % (i+1, len(self.children_d)))
            if data[0] == "filenode":
                mutable = data[1].get("mutable", False)
                writecap = to_str(data[1].get("rw_uri"))
                readcap = to_str(data[1].get("ro_uri"))
                self.children[name] = TahoeFileSource(self.nodeurl, mutable,
                                                      writecap, readcap, name)
            elif data[0] == "dirnode":
                writecap = to_str(data[1].get("rw_uri"))
                readcap = to_str(data[1].get("ro_uri"))
                if writecap and writecap in self.cache:
                    child = self.cache[writecap]
                elif readcap and readcap in self.cache:
                    child = self.cache[readcap]
                else:
                    child = TahoeDirectorySource(self.nodeurl, self.cache,
                                                 self.progressfunc, name)
                    child.init_from_grid(writecap, readcap)
                    if writecap:
                        self.cache[writecap] = child
                    if readcap:
                        self.cache[readcap] = child
                    if recurse:
                        child.populate(recurse=True)
                self.children[name] = child
            else:
                # TODO: there should be an option to skip unknown nodes.
                raise TahoeError("Cannot copy unknown nodes (ticket #839). "
                                 "You probably need to use a later version of "
                                 "Tahoe-LAFS to copy this directory.")

class TahoeMissingTarget:
    def __init__(self, url):
        self.url = url

    def put_file(self, inf):
        # We want to replace this object in-place.
        if not hasattr(inf, "seek"):
            inf = inf.read()
        PUT(self.url, inf)
        # TODO: this always creates immutable files. We might want an option
        # to always create mutable files, or to copy mutable files into new
        # mutable files.

    def put_uri(self, filecap):
        # I'm not sure this will always work
        return PUT(self.url + "?t=uri", filecap)

class TahoeDirectoryTarget:
    def __init__(self, nodeurl, cache, progressfunc):
        self.nodeurl = nodeurl
        self.cache = cache
        self.progressfunc = progressfunc
        self.new_children = {}

    def init_from_parsed(self, parsed):
        nodetype, d = parsed
        self.writecap = to_str(d.get("rw_uri"))
        self.readcap = to_str(d.get("ro_uri"))
        self.mutable = d.get("mutable", False) # older nodes don't provide it
        self.children_d = dict( [(unicode(name),value)
                                 for (name,value)
                                 in d["children"].iteritems()] )
        self.children = None

    def init_from_grid(self, writecap, readcap):
        self.writecap = writecap
        self.readcap = readcap
        bestcap = writecap or readcap
        url = self.nodeurl + "uri/%s" % urllib.quote(bestcap)
        resp = do_http("GET", url + "?t=json")
        if resp.status != 200:
            raise HTTPError("Error examining target directory", resp)
        parsed = simplejson.loads(resp.read())
        nodetype, d = parsed
        assert nodetype == "dirnode"
        self.mutable = d.get("mutable", False) # older nodes don't provide it
        self.children_d = dict( [(unicode(name),value)
                                 for (name,value)
                                 in d["children"].iteritems()] )
        self.children = None

    def just_created(self, writecap):
        # TODO: maybe integrate this with the constructor
        self.writecap = writecap
        self.readcap = uri.from_string(writecap).get_readonly().to_string()
        self.mutable = True
        self.children_d = {}
        self.children = {}

    def populate(self, recurse):
        if self.children is not None:
            return
        self.children = {}
        for i,(name, data) in enumerate(self.children_d.items()):
            self.progressfunc("examining %d of %d" % (i+1, len(self.children_d)))
            if data[0] == "filenode":
                mutable = data[1].get("mutable", False)
                writecap = to_str(data[1].get("rw_uri"))
                readcap = to_str(data[1].get("ro_uri"))
                url = None
                if self.writecap:
                    url = self.nodeurl + "/".join(["uri",
                                                   urllib.quote(self.writecap),
                                                   urllib.quote(unicode_to_url(name))])
                self.children[name] = TahoeFileTarget(self.nodeurl, mutable,
                                                      writecap, readcap, url)
            elif data[0] == "dirnode":
                writecap = to_str(data[1].get("rw_uri"))
                readcap = to_str(data[1].get("ro_uri"))
                if writecap and writecap in self.cache:
                    child = self.cache[writecap]
                elif readcap and readcap in self.cache:
                    child = self.cache[readcap]
                else:
                    child = TahoeDirectoryTarget(self.nodeurl, self.cache,
                                                 self.progressfunc)
                    child.init_from_grid(writecap, readcap)
                    if writecap:
                        self.cache[writecap] = child
                    if readcap:
                        self.cache[readcap] = child
                    if recurse:
                        child.populate(recurse=True)
                self.children[name] = child
            else:
                # TODO: there should be an option to skip unknown nodes.
                raise TahoeError("Cannot copy unknown nodes (ticket #839). "
                                 "You probably need to use a later version of "
                                 "Tahoe-LAFS to copy this directory.")

    def get_child_target(self, name):
        # return a new target for a named subdirectory of this dir
        precondition(isinstance(name, unicode), name)
        if self.children is None:
            self.populate(recurse=False)
        if name in self.children:
            return self.children[name]
        writecap = make_tahoe_subdirectory(self.nodeurl, self.writecap, name)
        child = TahoeDirectoryTarget(self.nodeurl, self.cache,
                                     self.progressfunc)
        child.just_created(writecap)
        self.children[name] = child
        return child

    def put_file(self, name, inf):
        precondition(isinstance(name, unicode), name)
        url = self.nodeurl + "uri"
        if not hasattr(inf, "seek"):
            inf = inf.read()

        if self.children is None:
            self.populate(recurse=False)

        # Check to see if we already have a mutable file by this name.
        # If so, overwrite that file in place.
        if name in self.children and self.children[name].mutable:
            self.children[name].put_file(inf)
        else:
            filecap = PUT(url, inf)
            # TODO: this always creates immutable files. We might want an option
            # to always create mutable files, or to copy mutable files into new
            # mutable files.
            self.new_children[name] = filecap

    def put_uri(self, name, filecap):
        precondition(isinstance(name, unicode), name)
        self.new_children[name] = filecap

    def set_children(self):
        if not self.new_children:
            return
        url = (self.nodeurl + "uri/" + urllib.quote(self.writecap)
               + "?t=set_children")
        set_data = {}
        for (name, filecap) in self.new_children.items():
            # it just so happens that ?t=set_children will accept both file
            # read-caps and write-caps as ['rw_uri'], and will handle either
            # correctly. So don't bother trying to figure out whether the one
            # we have is read-only or read-write.
            # TODO: think about how this affects forward-compatibility for
            # unknown caps
            set_data[name] = ["filenode", {"rw_uri": filecap}]
        body = simplejson.dumps(set_data)
        POST(url, body)

FileSources = (LocalFileSource, TahoeFileSource)
DirectorySources = (LocalDirectorySource, TahoeDirectorySource)
FileTargets = (LocalFileTarget, TahoeFileTarget)
DirectoryTargets = (LocalDirectoryTarget, TahoeDirectoryTarget)
MissingTargets = (LocalMissingTarget, TahoeMissingTarget)

class Copier:

    def do_copy(self, options, progressfunc=None):
        if options['quiet']:
            verbosity = 0
        elif options['verbose']:
            verbosity = 2
        else:
            verbosity = 1

        nodeurl = options['node-url']
        if nodeurl[-1] != "/":
            nodeurl += "/"
        self.nodeurl = nodeurl
        self.progressfunc = progressfunc
        self.options = options
        self.aliases = options.aliases
        self.verbosity = verbosity
        self.stdout = options.stdout
        self.stderr = options.stderr
        if verbosity >= 2 and not self.progressfunc:
            def progress(message):
                print >>self.stderr, message
            self.progressfunc = progress
        self.caps_only = options["caps-only"]
        self.cache = {}
        try:
            status = self.try_copy()
            return status
        except TahoeError, te:
            if verbosity >= 2:
                Failure().printTraceback(self.stderr)
                print >>self.stderr
            te.display(self.stderr)
            return 1

    def try_copy(self):
        """
        All usage errors (except for target filename collisions) are caught
        here, not in a subroutine. This bottoms out in copy_file_to_file() or
        copy_things_to_directory().
        """
        source_specs = self.options.sources
        destination_spec = self.options.destination
        recursive = self.options["recursive"]

        target = self.get_target_info(destination_spec)
        precondition(isinstance(target, FileTargets + DirectoryTargets + MissingTargets), target)
        target_has_trailing_slash = destination_spec.endswith("/")

        sources = [] # list of source objects
        for ss in source_specs:
            try:
                si = self.get_source_info(ss)
            except FilenameWithTrailingSlashError as e:
                self.to_stderr(str(e))
                return 1
            precondition(isinstance(si, FileSources + DirectorySources), si)
            sources.append(si)

        # if any source is a directory, must use -r
        # if target is missing:
        #    if source is a single file, target will be a file
        #    else target will be a directory, so mkdir it
        # if there are multiple sources, target must be a dir
        # if target is a file, source must be a single file
        # if target is directory, sources must be named or a dir

        have_source_dirs = any([isinstance(s, DirectorySources)
                                for s in sources])
        if have_source_dirs and not recursive:
            # 'cp dir target' without -r: error
            self.to_stderr("cannot copy directories without --recursive")
            return 1
        del recursive # -r is only used for signalling errors

        if isinstance(target, FileTargets):
            target_is_file = True
        elif isinstance(target, DirectoryTargets):
            target_is_file = False
        else: # isinstance(target, MissingTargets)
            if len(sources) == 1 and isinstance(sources[0], FileSources):
                target_is_file = True
            else:
                target_is_file = False

        if target_is_file and target_has_trailing_slash:
            self.to_stderr("target is not a directory, but ends with a slash")
            return 1

        if len(sources) > 1 and target_is_file:
            self.to_stderr("copying multiple things requires target be a directory")
            return 1

        if target_is_file:
            _assert(len(sources) == 1, sources)
            if not isinstance(sources[0], FileSources):
                # 'cp -r dir existingfile': error
                self.to_stderr("cannot copy directory into a file")
                return 1
            return self.copy_file_to_file(sources[0], target)

        # else target is a directory, so each source must be one of:
        # * a named file (copied to a new file under the target)
        # * a named directory (causes a new directory of the same name to be
        #   created under the target, then the contents of the source are
        #   copied into that directory)
        # * an unnamed directory (the contents of the source are copied into
        #   the target, without a new directory being made)
        #
        # If any source is an unnamed file, throw an error, since we have no
        # way to name the output file.
        _assert(isinstance(target, DirectoryTargets + MissingTargets), target)

        for source in sources:
            if isinstance(source, FileSources) and source.basename() is None:
                self.to_stderr("when copying into a directory, all source files must have names, but %s is unnamed" % quote_output(source_specs[0]))
                return 1
        return self.copy_things_to_directory(sources, target)

    def to_stderr(self, text):
        print >>self.stderr, text

    # FIXME reduce the amount of near-duplicate code between get_target_info
    # and get_source_info.

    def get_target_info(self, destination_spec):
        precondition(isinstance(destination_spec, unicode), destination_spec)
        rootcap, path_utf8 = get_alias(self.aliases, destination_spec, None)
        path = path_utf8.decode("utf-8")
        if rootcap == DefaultAliasMarker:
            # no alias, so this is a local file
            pathname = abspath_expanduser_unicode(path)
            if not os.path.exists(pathname):
                t = LocalMissingTarget(pathname)
            elif os.path.isdir(pathname):
                t = LocalDirectoryTarget(self.progress, pathname)
            else:
                # TODO: should this be _assert? what happens if the target is
                # a special file?
                assert os.path.isfile(pathname), pathname
                t = LocalFileTarget(pathname) # non-empty
        else:
            # this is a tahoe object
            url = self.nodeurl + "uri/%s" % urllib.quote(rootcap)
            if path:
                url += "/" + escape_path(path)

            resp = do_http("GET", url + "?t=json")
            if resp.status == 404:
                # doesn't exist yet
                t = TahoeMissingTarget(url)
            elif resp.status == 200:
                parsed = simplejson.loads(resp.read())
                nodetype, d = parsed
                if nodetype == "dirnode":
                    t = TahoeDirectoryTarget(self.nodeurl, self.cache,
                                             self.progress)
                    t.init_from_parsed(parsed)
                else:
                    writecap = to_str(d.get("rw_uri"))
                    readcap = to_str(d.get("ro_uri"))
                    mutable = d.get("mutable", False)
                    t = TahoeFileTarget(self.nodeurl, mutable,
                                        writecap, readcap, url)
            else:
                raise HTTPError("Error examining target %s"
                                 % quote_output(destination_spec), resp)
        return t

    def get_source_info(self, source_spec):
        """
        This turns an argv string into a (Local|Tahoe)(File|Directory)Source.
        """
        precondition(isinstance(source_spec, unicode), source_spec)
        rootcap, path_utf8 = get_alias(self.aliases, source_spec, None)
        path = path_utf8.decode("utf-8")
        # any trailing slash is removed in abspath_expanduser_unicode(), so
        # make a note of it here, to throw an error later
        had_trailing_slash = path.endswith("/")
        if rootcap == DefaultAliasMarker:
            # no alias, so this is a local file
            pathname = abspath_expanduser_unicode(path)
            name = os.path.basename(pathname)
            if not os.path.exists(pathname):
                raise MissingSourceError(source_spec, quotefn=quote_local_unicode_path)
            if os.path.isdir(pathname):
                t = LocalDirectorySource(self.progress, pathname, name)
            else:
                if had_trailing_slash:
                    raise FilenameWithTrailingSlashError(source_spec,
                                                         quotefn=quote_local_unicode_path)
                if not os.path.isfile(pathname):
                    raise WeirdSourceError(pathname)
                t = LocalFileSource(pathname, name) # non-empty
        else:
            # this is a tahoe object
            url = self.nodeurl + "uri/%s" % urllib.quote(rootcap)
            name = None
            if path:
                if path.endswith("/"):
                    path = path[:-1]
                url += "/" + escape_path(path)
                last_slash = path.rfind(u"/")
                name = path
                if last_slash != -1:
                    name = path[last_slash+1:]

            resp = do_http("GET", url + "?t=json")
            if resp.status == 404:
                raise MissingSourceError(source_spec)
            elif resp.status != 200:
                raise HTTPError("Error examining source %s" % quote_output(source_spec),
                                resp)
            parsed = simplejson.loads(resp.read())
            nodetype, d = parsed
            if nodetype == "dirnode":
                t = TahoeDirectorySource(self.nodeurl, self.cache,
                                         self.progress, name)
                t.init_from_parsed(parsed)
            else:
                if had_trailing_slash:
                    raise FilenameWithTrailingSlashError(source_spec)
                writecap = to_str(d.get("rw_uri"))
                readcap = to_str(d.get("ro_uri"))
                mutable = d.get("mutable", False) # older nodes don't provide it
                t = TahoeFileSource(self.nodeurl, mutable, writecap, readcap, name)
        return t


    def need_to_copy_bytes(self, source, target):
        if source.need_to_copy_bytes:
            # mutable tahoe files, and local files
            return True
        if isinstance(target, (LocalFileTarget, LocalDirectoryTarget)):
            return True
        return False

    def announce_success(self, msg):
        if self.verbosity >= 1:
            print >>self.stdout, "Success: %s" % msg
        return 0

    def copy_file_to_file(self, source, target):
        precondition(isinstance(source, FileSources), source)
        precondition(isinstance(target, FileTargets + MissingTargets), target)
        if self.need_to_copy_bytes(source, target):
            # if the target is a local directory, this will just write the
            # bytes to disk. If it is a tahoe directory, it will upload the
            # data, and stash the new filecap for a later set_children call.
            f = source.open(self.caps_only)
            target.put_file(f)
            return self.announce_success("file copied")
        # otherwise we're copying tahoe to tahoe, and using immutable files,
        # so we can just make a link. TODO: this probably won't always work:
        # need to enumerate the cases and analyze them.
        target.put_uri(source.bestcap())
        return self.announce_success("file linked")

    def copy_things_to_directory(self, sources, target):
        # step one: if the target is missing, we should mkdir it
        target = self.maybe_create_target(target)
        target.populate(recurse=False)

        # step two: scan any source dirs, recursively, to find children
        for s in sources:
            if isinstance(s, DirectorySources):
                s.populate(recurse=True)
            if isinstance(s, FileSources):
                # each source must have a name, or be a directory
                _assert(s.basename() is not None, s)

        # step three: find a target for each source node, creating
        # directories as necessary. 'targetmap' is a dictionary that uses
        # target Directory instances as keys, and has values of (name:
        # sourceobject) dicts for all the files that need to wind up there.
        targetmap = self.build_targetmap(sources, target)

        # target name collisions are an error
        collisions = []
        for target, sources in targetmap.items():
            target_names = {}
            for source in sources:
                name = source.basename()
                if name in target_names:
                    collisions.append((target, source, target_names[name]))
                else:
                    target_names[name] = source
        if collisions:
            self.to_stderr("cannot copy multiple files with the same name into the same target directory")
            # I'm not sure how to show where the collisions are coming from
            #for (target, source1, source2) in collisions:
            #    self.to_stderr(source1.basename())
            return 1

        # step four: walk through the list of targets. For each one, copy all
        # the files. If the target is a TahoeDirectory, upload and create
        # read-caps, then do a set_children to the target directory.
        self.copy_to_targetmap(targetmap)

        return self.announce_success("files copied")

    def maybe_create_target(self, target):
        if isinstance(target, LocalMissingTarget):
            os.makedirs(target.pathname)
            target = LocalDirectoryTarget(self.progress, target.pathname)
        elif isinstance(target, TahoeMissingTarget):
            writecap = mkdir(target.url)
            target = TahoeDirectoryTarget(self.nodeurl, self.cache,
                                          self.progress)
            target.just_created(writecap)
        # afterwards, or otherwise, it will be a directory
        precondition(isinstance(target, DirectoryTargets), target)
        return target

    def build_targetmap(self, sources, target):
        num_source_files = len([s for s in sources
                                if isinstance(s, FileSources)])
        num_source_dirs = len([s for s in sources
                               if isinstance(s, DirectorySources)])
        self.progress("attaching sources to targets, "
                      "%d files / %d dirs in root" %
                      (num_source_files, num_source_dirs))

        # this maps each target directory to a list of source files that need
        # to be copied into it. All source files have names.
        targetmap = defaultdict(list)

        for s in sources:
            if isinstance(s, FileSources):
                targetmap[target].append(s)
            else:
                _assert(isinstance(s, DirectorySources), s)
                name = s.basename()
                if name is not None:
                    # named sources get a new directory. see #2329
                    new_target = target.get_child_target(name)
                else:
                    # unnamed sources have their contents copied directly
                    new_target = target
                self.assign_targets(targetmap, s, new_target)

        self.progress("targets assigned, %s dirs, %s files" %
                      (len(targetmap), self.count_files_to_copy(targetmap)))
        return targetmap

    def assign_targets(self, targetmap, source, target):
        # copy everything in the source into the target
        precondition(isinstance(source, DirectorySources), source)
        for name, child in source.children.items():
            if isinstance(child, DirectorySources):
                # we will need a target directory for this one
                subtarget = target.get_child_target(name)
                self.assign_targets(targetmap, child, subtarget)
            else:
                _assert(isinstance(child, FileSources), child)
                targetmap[target].append(child)

    def copy_to_targetmap(self, targetmap):
        files_to_copy = self.count_files_to_copy(targetmap)
        self.progress("starting copy, %d files, %d directories" %
                      (files_to_copy, len(targetmap)))
        files_copied = 0
        targets_finished = 0

        for target, sources in targetmap.items():
            _assert(isinstance(target, DirectoryTargets), target)
            for source in sources:
                _assert(isinstance(source, FileSources), source)
                self.copy_file_into_dir(source, source.basename(), target)
                files_copied += 1
                self.progress("%d/%d files, %d/%d directories" %
                              (files_copied, files_to_copy,
                               targets_finished, len(targetmap)))
            target.set_children()
            targets_finished += 1
            self.progress("%d/%d directories" %
                          (targets_finished, len(targetmap)))

    def count_files_to_copy(self, targetmap):
        return sum([len(sources) for sources in targetmap.values()])

    def copy_file_into_dir(self, source, name, target):
        precondition(isinstance(source, FileSources), source)
        precondition(isinstance(target, DirectoryTargets), target)
        precondition(isinstance(name, unicode), name)
        if self.need_to_copy_bytes(source, target):
            # if the target is a local directory, this will just write the
            # bytes to disk. If it is a tahoe directory, it will upload the
            # data, and stash the new filecap for a later set_children call.
            f = source.open(self.caps_only)
            target.put_file(name, f)
            return
        # otherwise we're copying tahoe to tahoe, and using immutable files,
        # so we can just make a link
        target.put_uri(name, source.bestcap())


    def progress(self, message):
        #print message
        if self.progressfunc:
            self.progressfunc(message)


def copy(options):
    return Copier().do_copy(options)

# error cases that need improvement:
#  local-file-in-the-way
#   touch proposed
#   tahoe cp -r my:docs/proposed/denver.txt proposed/denver.txt
#  handling of unknown nodes

# things that maybe should be errors but aren't
#  local-dir-in-the-way
#   mkdir denver.txt
#   tahoe cp -r my:docs/proposed/denver.txt denver.txt
#   (creates denver.txt/denver.txt)

# error cases that look good:
#  tahoe cp -r my:docs/missing missing
#  disconnect servers
#   tahoe cp -r my:docs/missing missing  -> No JSON object could be decoded
#  tahoe-file-in-the-way (when we want to make a directory)
#   tahoe put README my:docs
#   tahoe cp -r docs/proposed my:docs/proposed
