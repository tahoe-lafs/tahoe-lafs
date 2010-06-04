
import os.path
import urllib
import simplejson
from cStringIO import StringIO
from twisted.python.failure import Failure
from allmydata.scripts.common import get_alias, escape_path, \
                                     DefaultAliasMarker, UnknownAliasError
from allmydata.scripts.common_http import do_http
from allmydata import uri
from allmydata.util.stringutils import unicode_to_url, listdir_unicode, open_unicode
from allmydata.util.assertutil import precondition


def ascii_or_none(s):
    if s is None:
        return s
    return str(s)

class TahoeError(Exception):
    def __init__(self, msg, resp):
        self.msg = msg
        self.status = resp.status
        self.reason = resp.reason
        self.body = resp.read()

    def display(self, err):
        print >>err, "%s: %s %s" % (self.msg, self.status, self.reason)
        print >>err, self.body

class MissingSourceError(Exception):
    pass

def GET_to_file(url):
    resp = do_http("GET", url)
    if resp.status == 200:
        return resp
    raise TahoeError("Error during GET", resp)

def GET_to_string(url):
    f = GET_to_file(url)
    return f.read()

def PUT(url, data):
    resp = do_http("PUT", url, data)
    if resp.status in (200, 201):
        return resp.read()
    raise TahoeError("Error during PUT", resp)

def POST(url, data):
    resp = do_http("POST", url, data)
    if resp.status in (200, 201):
        return resp.read()
    raise TahoeError("Error during POST", resp)

def mkdir(targeturl):
    url = targeturl + "?t=mkdir"
    resp = do_http("POST", url)
    if resp.status in (200, 201):
        return resp.read().strip()
    raise TahoeError("Error during mkdir", resp)

def make_tahoe_subdirectory(nodeurl, parent_writecap, name):
    url = nodeurl + "/".join(["uri",
                              urllib.quote(parent_writecap),
                              urllib.quote(name),
                              ]) + "?t=mkdir"
    resp = do_http("POST", url)
    if resp.status in (200, 201):
        return resp.read().strip()
    raise TahoeError("Error during mkdir", resp)


class LocalFileSource:
    def __init__(self, pathname):
        precondition(isinstance(pathname, unicode), pathname)
        self.pathname = pathname

    def need_to_copy_bytes(self):
        return True

    def open(self, caps_only):
        return open(self.pathname, "rb")

class LocalFileTarget:
    def __init__(self, pathname):
        precondition(isinstance(pathname, unicode), pathname)
        self.pathname = pathname
    def put_file(self, inf):
        outf = open(self.pathname, "wb")
        while True:
            data = inf.read(32768)
            if not data:
                break
            outf.write(data)
        outf.close()

class LocalMissingTarget:
    def __init__(self, pathname):
        precondition(isinstance(pathname, unicode), pathname)
        self.pathname = pathname

    def put_file(self, inf):
        outf = open(self.pathname, "wb")
        while True:
            data = inf.read(32768)
            if not data:
                break
            outf.write(data)
        outf.close()

class LocalDirectorySource:
    def __init__(self, progressfunc, pathname):
        precondition(isinstance(pathname, unicode), pathname)

        self.progressfunc = progressfunc
        self.pathname = pathname
        self.children = None

    def populate(self, recurse):
        if self.children is not None:
            return
        self.children = {}
        children = listdir_unicode(self.pathname)
        for i,n in enumerate(children):
            self.progressfunc("examining %d of %d" % (i, len(children)))
            pn = os.path.join(self.pathname, n)
            if os.path.isdir(pn):
                child = LocalDirectorySource(self.progressfunc, pn)
                self.children[n] = child
                if recurse:
                    child.populate(True)
            elif os.path.isfile(pn):
                self.children[n] = LocalFileSource(pn)
            else:
                # Could be dangling symlink; probably not copy-able.
                pass

class LocalDirectoryTarget:
    def __init__(self, progressfunc, pathname):
        precondition(isinstance(pathname, unicode), pathname)

        self.progressfunc = progressfunc
        self.pathname = pathname
        self.children = None

    def populate(self, recurse):
        if self.children is not None:
            return
        self.children = {}
        children = listdir_unicode(self.pathname)
        for i,n in enumerate(children):
            self.progressfunc("examining %d of %d" % (i, len(children)))
            pn = os.path.join(self.pathname, n)
            if os.path.isdir(pn):
                child = LocalDirectoryTarget(self.progressfunc, pn)
                self.children[n] = child
                if recurse:
                    child.populate(True)
            else:
                assert os.path.isfile(pn)
                self.children[n] = LocalFileTarget(pn)

    def get_child_target(self, name):
        if self.children is None:
            self.populate(False)
        if name in self.children:
            return self.children[name]
        pathname = os.path.join(self.pathname, name)
        os.makedirs(pathname)
        return LocalDirectoryTarget(self.progressfunc, pathname)

    def put_file(self, name, inf):
        precondition(isinstance(name, unicode), name)
        pathname = os.path.join(self.pathname, name)
        outf = open_unicode(pathname, "wb")
        while True:
            data = inf.read(32768)
            if not data:
                break
            outf.write(data)
        outf.close()

    def set_children(self):
        pass

class TahoeFileSource:
    def __init__(self, nodeurl, mutable, writecap, readcap):
        self.nodeurl = nodeurl
        self.mutable = mutable
        self.writecap = writecap
        self.readcap = readcap

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
    def __init__(self, nodeurl, cache, progressfunc):
        self.nodeurl = nodeurl
        self.cache = cache
        self.progressfunc = progressfunc

    def init_from_grid(self, writecap, readcap):
        self.writecap = writecap
        self.readcap = readcap
        bestcap = writecap or readcap
        url = self.nodeurl + "uri/%s" % urllib.quote(bestcap)
        resp = do_http("GET", url + "?t=json")
        if resp.status != 200:
            raise TahoeError("Error examining source directory", resp)
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
        self.writecap = ascii_or_none(d.get("rw_uri"))
        self.readcap = ascii_or_none(d.get("ro_uri"))
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
            self.progressfunc("examining %d of %d" % (i, len(self.children_d)))
            if data[0] == "filenode":
                mutable = data[1].get("mutable", False)
                writecap = ascii_or_none(data[1].get("rw_uri"))
                readcap = ascii_or_none(data[1].get("ro_uri"))
                self.children[name] = TahoeFileSource(self.nodeurl, mutable,
                                                      writecap, readcap)
            elif data[0] == "dirnode":
                writecap = ascii_or_none(data[1].get("rw_uri"))
                readcap = ascii_or_none(data[1].get("ro_uri"))
                if writecap and writecap in self.cache:
                    child = self.cache[writecap]
                elif readcap and readcap in self.cache:
                    child = self.cache[readcap]
                else:
                    child = TahoeDirectorySource(self.nodeurl, self.cache,
                                                 self.progressfunc)
                    child.init_from_grid(writecap, readcap)
                    if writecap:
                        self.cache[writecap] = child
                    if readcap:
                        self.cache[readcap] = child
                    if recurse:
                        child.populate(True)
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
        self.writecap = ascii_or_none(d.get("rw_uri"))
        self.readcap = ascii_or_none(d.get("ro_uri"))
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
            raise TahoeError("Error examining target directory", resp)
        parsed = simplejson.loads(resp.read())
        nodetype, d = parsed
        assert nodetype == "dirnode"
        self.mutable = d.get("mutable", False) # older nodes don't provide it
        self.children_d = dict( [(unicode(name),value)
                                 for (name,value)
                                 in d["children"].iteritems()] )
        self.children = None

    def just_created(self, writecap):
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
            self.progressfunc("examining %d of %d" % (i, len(self.children_d)))
            if data[0] == "filenode":
                mutable = data[1].get("mutable", False)
                writecap = ascii_or_none(data[1].get("rw_uri"))
                readcap = ascii_or_none(data[1].get("ro_uri"))
                url = None
                if self.writecap:
                    url = self.nodeurl + "/".join(["uri",
                                                   urllib.quote(self.writecap),
                                                   urllib.quote(unicode_to_url(name))])
                self.children[name] = TahoeFileTarget(self.nodeurl, mutable,
                                                      writecap, readcap, url)
            elif data[0] == "dirnode":
                writecap = ascii_or_none(data[1].get("rw_uri"))
                readcap = ascii_or_none(data[1].get("ro_uri"))
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
                        child.populate(True)
                self.children[name] = child
            else:
                # TODO: there should be an option to skip unknown nodes.
                raise TahoeError("Cannot copy unknown nodes (ticket #839). "
                                 "You probably need to use a later version of "
                                 "Tahoe-LAFS to copy this directory.")

    def get_child_target(self, name):
        # return a new target for a named subdirectory of this dir
        if self.children is None:
            self.populate(False)
        if name in self.children:
            return self.children[name]
        writecap = make_tahoe_subdirectory(self.nodeurl, self.writecap, name)
        child = TahoeDirectoryTarget(self.nodeurl, self.cache,
                                     self.progressfunc)
        child.just_created(writecap)
        self.children[name] = child
        return child

    def put_file(self, name, inf):
        url = self.nodeurl + "uri"
        if not hasattr(inf, "seek"):
            inf = inf.read()
        filecap = PUT(url, inf)
        # TODO: this always creates immutable files. We might want an option
        # to always create mutable files, or to copy mutable files into new
        # mutable files.
        self.new_children[name] = filecap

    def put_uri(self, name, filecap):
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
            Failure().printTraceback(self.stderr)
            print >>self.stderr
            te.display(self.stderr)
            return 1

    def try_copy(self):
        source_specs = self.options.sources
        destination_spec = self.options.destination
        recursive = self.options["recursive"]

        try:
            target = self.get_target_info(destination_spec)
        except UnknownAliasError, e:
            self.to_stderr("error: %s" % e.args[0])
            return 1

        try:
            sources = [] # list of (name, source object)
            for ss in source_specs:
                name, source = self.get_source_info(ss)
                sources.append( (name, source) )
        except MissingSourceError, e:
            self.to_stderr("No such file or directory %s" % e.args[0])
            return 1
        except UnknownAliasError, e:
            self.to_stderr("error: %s" % e.args[0])
            return 1

        have_source_dirs = bool([s for (name,s) in sources
                                 if isinstance(s, (LocalDirectorySource,
                                                   TahoeDirectorySource))])

        if have_source_dirs and not recursive:
            self.to_stderr("cannot copy directories without --recursive")
            return 1

        if isinstance(target, (LocalFileTarget, TahoeFileTarget)):
            # cp STUFF foo.txt, where foo.txt already exists. This limits the
            # possibilities considerably.
            if len(sources) > 1:
                self.to_stderr("target '%s' is not a directory" % destination_spec)
                return 1
            if have_source_dirs:
                self.to_stderr("cannot copy directory into a file")
                return 1
            name, source = sources[0]
            return self.copy_file(source, target)

        if isinstance(target, (LocalMissingTarget, TahoeMissingTarget)):
            if recursive:
                return self.copy_to_directory(sources, target)
            if len(sources) > 1:
                # if we have -r, we'll auto-create the target directory. Without
                # it, we'll only create a file.
                self.to_stderr("cannot copy multiple files into a file without -r")
                return 1
            # cp file1 newfile
            name, source = sources[0]
            return self.copy_file(source, target)

        if isinstance(target, (LocalDirectoryTarget, TahoeDirectoryTarget)):
            # We're copying to an existing directory -- make sure that we 
            # have target names for everything
            for (name, source) in sources:
                if name is None and isinstance(source, TahoeFileSource):
                    self.to_stderr(
                        "error: you must specify a destination filename")
                    return 1
            return self.copy_to_directory(sources, target)

        self.to_stderr("unknown target")
        return 1

    def to_stderr(self, text):
        print >>self.stderr, text

    def get_target_info(self, destination_spec):
        rootcap, path = get_alias(self.aliases, destination_spec, None)
        if rootcap == DefaultAliasMarker:
            # no alias, so this is a local file
            pathname = os.path.abspath(os.path.expanduser(path))
            if not os.path.exists(pathname):
                t = LocalMissingTarget(pathname)
            elif os.path.isdir(pathname):
                t = LocalDirectoryTarget(self.progress, pathname)
            else:
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
                    writecap = ascii_or_none(d.get("rw_uri"))
                    readcap = ascii_or_none(d.get("ro_uri"))
                    mutable = d.get("mutable", False)
                    t = TahoeFileTarget(self.nodeurl, mutable,
                                        writecap, readcap, url)
            else:
                raise TahoeError("Error examining target '%s'"
                                 % destination_spec, resp)
        return t

    def get_source_info(self, source_spec):
        rootcap, path = get_alias(self.aliases, source_spec, None)
        if rootcap == DefaultAliasMarker:
            # no alias, so this is a local file
            pathname = os.path.abspath(os.path.expanduser(path))
            name = os.path.basename(pathname)
            if not os.path.exists(pathname):
                raise MissingSourceError(source_spec)
            if os.path.isdir(pathname):
                t = LocalDirectorySource(self.progress, pathname)
            else:
                assert os.path.isfile(pathname)
                t = LocalFileSource(pathname) # non-empty
        else:
            # this is a tahoe object
            url = self.nodeurl + "uri/%s" % urllib.quote(rootcap)
            name = None
            if path:
                url += "/" + escape_path(path)
                last_slash = path.rfind("/")
                name = path
                if last_slash:
                    name = path[last_slash+1:]

            resp = do_http("GET", url + "?t=json")
            if resp.status == 404:
                raise MissingSourceError(source_spec)
            elif resp.status != 200:
                raise TahoeError("Error examining source '%s'" % source_spec,
                                 resp)
            parsed = simplejson.loads(resp.read())
            nodetype, d = parsed
            if nodetype == "dirnode":
                t = TahoeDirectorySource(self.nodeurl, self.cache,
                                         self.progress)
                t.init_from_parsed(parsed)
            else:
                writecap = ascii_or_none(d.get("rw_uri"))
                readcap = ascii_or_none(d.get("ro_uri"))
                mutable = d.get("mutable", False) # older nodes don't provide it
                if source_spec.rfind('/') != -1:
                    name = source_spec[source_spec.rfind('/')+1:]
                t = TahoeFileSource(self.nodeurl, mutable, writecap, readcap)
        return name, t


    def dump_graph(self, s, indent=" "):
        for name, child in s.children.items():
            print indent + name + ":" + str(child)
            if isinstance(child, (LocalDirectorySource, TahoeDirectorySource)):
                self.dump_graph(child, indent+"  ")

    def copy_to_directory(self, source_infos, target):
        # step one: build a recursive graph of the source tree. This returns
        # a dictionary, with child names as keys, and values that are either
        # Directory or File instances (local or tahoe).
        source_dirs = self.build_graphs(source_infos)
        source_files = [source for source in source_infos
                        if isinstance(source[1], (LocalFileSource,
                                                  TahoeFileSource))]

        #print "graphs"
        #for s in source_dirs:
        #    self.dump_graph(s)

        # step two: create the top-level target directory object
        if isinstance(target, LocalMissingTarget):
            os.makedirs(target.pathname)
            target = LocalDirectoryTarget(self.progress, target.pathname)
        elif isinstance(target, TahoeMissingTarget):
            writecap = mkdir(target.url)
            target = TahoeDirectoryTarget(self.nodeurl, self.cache,
                                          self.progress)
            target.just_created(writecap)
        assert isinstance(target, (LocalDirectoryTarget, TahoeDirectoryTarget))
        target.populate(False)

        # step three: find a target for each source node, creating
        # directories as necessary. 'targetmap' is a dictionary that uses
        # target Directory instances as keys, and has values of
        # (name->sourceobject) dicts for all the files that need to wind up
        # there.

        # sources are all LocalFile/LocalDirectory/TahoeFile/TahoeDirectory
        # target is LocalDirectory/TahoeDirectory

        self.progress("attaching sources to targets, "
                      "%d files / %d dirs in root" %
                      (len(source_files), len(source_dirs)))

        self.targetmap = {}
        self.files_to_copy = 0

        for (name,s) in source_files:
            self.attach_to_target(s, name, target)
            self.files_to_copy += 1

        for source in source_dirs:
            self.assign_targets(source, target)

        self.progress("targets assigned, %s dirs, %s files" %
                      (len(self.targetmap), self.files_to_copy))

        self.progress("starting copy, %d files, %d directories" %
                      (self.files_to_copy, len(self.targetmap)))
        self.files_copied = 0
        self.targets_finished = 0

        # step four: walk through the list of targets. For each one, copy all
        # the files. If the target is a TahoeDirectory, upload and create
        # read-caps, then do a set_children to the target directory.

        for target in self.targetmap:
            self.copy_files_to_target(self.targetmap[target], target)
            self.targets_finished += 1
            self.progress("%d/%d directories" %
                          (self.targets_finished, len(self.targetmap)))

        return self.announce_success("files copied")

    def attach_to_target(self, source, name, target):
        if target not in self.targetmap:
            self.targetmap[target] = {}
        self.targetmap[target][name] = source
        self.files_to_copy += 1

    def assign_targets(self, source, target):
        # copy everything in the source into the target
        assert isinstance(source, (LocalDirectorySource, TahoeDirectorySource))

        for name, child in source.children.items():
            if isinstance(child, (LocalDirectorySource, TahoeDirectorySource)):
                # we will need a target directory for this one
                subtarget = target.get_child_target(name)
                self.assign_targets(child, subtarget)
            else:
                assert isinstance(child, (LocalFileSource, TahoeFileSource))
                self.attach_to_target(child, name, target)



    def copy_files_to_target(self, targetmap, target):
        for name, source in targetmap.items():
            assert isinstance(source, (LocalFileSource, TahoeFileSource))
            self.copy_file_into(source, name, target)
            self.files_copied += 1
            self.progress("%d/%d files, %d/%d directories" %
                          (self.files_copied, self.files_to_copy,
                           self.targets_finished, len(self.targetmap)))
        target.set_children()

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

    def copy_file(self, source, target):
        assert isinstance(source, (LocalFileSource, TahoeFileSource))
        assert isinstance(target, (LocalFileTarget, TahoeFileTarget,
                                   LocalMissingTarget, TahoeMissingTarget))
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

    def copy_file_into(self, source, name, target):
        assert isinstance(source, (LocalFileSource, TahoeFileSource))
        assert isinstance(target, (LocalDirectoryTarget, TahoeDirectoryTarget))
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

    def build_graphs(self, source_infos):
        graphs = []
        for name,source in source_infos:
            if isinstance(source, (LocalDirectorySource, TahoeDirectorySource)):
                source.populate(True)
                graphs.append(source)
        return graphs


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
