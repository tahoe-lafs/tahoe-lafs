
import os.path
import urllib
import simplejson
from allmydata.scripts.common import get_alias, escape_path, DefaultAliasMarker
from allmydata.scripts.common_http import do_http
from allmydata import uri

def ascii_or_none(s):
    if s is None:
        return s
    return str(s)

class WriteError(Exception):
    pass
class ReadError(Exception):
    pass

def GET_to_file(url):
    resp = do_http("GET", url)
    if resp.status == 200:
        return resp
    raise ReadError("Error during GET: %s %s %s" % (resp.status,
                                                    resp.reason,
                                                    resp.read()))
def GET_to_string(url):
    f = GET_to_file(url)
    return f.read()

def PUT(url, data):
    resp = do_http("PUT", url, data)
    if resp.status in (200, 201):
        return resp.read()
    raise WriteError("Error during PUT: %s %s %s" % (resp.status, resp.reason,
                                                     resp.read()))

def mkdir(targeturl):
    resp = do_http("POST", targeturl)
    if resp.status in (200, 201):
        return resp.read().strip()
    raise WriteError("Error during mkdir: %s %s %s" % (resp.status, resp.reason,
                                                       resp.read()))

def make_tahoe_subdirectory(nodeurl, parent_writecap, name):
    url = nodeurl + "/".join(["uri",
                              urllib.quote(parent_writecap),
                              urllib.quote(name),
                              ]) + "?t=mkdir"
    resp = do_http("POST", url)
    if resp.status in (200, 201):
        return resp.read().strip()
    raise WriteError("Error during mkdir: %s %s %s" % (resp.status, resp.reason,
                                                       resp.read()))


class LocalFileSource:
    def __init__(self, pathname):
        self.pathname = pathname

    def need_to_copy_bytes(self):
        return True

    def open(self):
        return open(self.pathname, "rb")

class LocalFileTarget:
    def __init__(self, pathname):
        self.pathname = pathname

class LocalDirectorySource:
    def __init__(self, progressfunc, pathname):
        self.progressfunc = progressfunc
        self.pathname = pathname
        self.children = None

    def populate(self, recurse):
        children = os.listdir(self.pathname)
        for i,n in enumerate(children):
            self.progressfunc("examining %d of %d" % (i, len(children)))
            pn = os.path.join(self.pathname, n)
            if os.path.isdir(pn):
                child = LocalDirectorySource(self.progressfunc, pn)
                self.children[n] = child
                if recurse:
                    child.populate(True)
            else:
                assert os.path.isfile(pn)
                self.children[n] = LocalFileSource(pn)

class LocalDirectoryTarget:
    def __init__(self, progressfunc, pathname):
        self.progressfunc = progressfunc
        self.pathname = pathname
        self.children = None

    def populate(self, recurse):
        children = os.listdir(self.pathname)
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
        pathname = os.path.join(self.pathname, name)
        outf = open(pathname, "wb")
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

    def open(self):
        url = self.nodeurl + "uri/" + urllib.quote(self.readcap)
        return GET_to_file(url)

    def bestcap(self):
        return self.writecap or self.readcap

class TahoeFileTarget:
    def __init__(self, nodeurl, mutable, writecap, readcap):
        self.nodeurl = nodeurl
        self.mutable = mutable
        self.writecap = writecap
        self.readcap = readcap

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
        assert resp.status == 200
        parsed = simplejson.loads(resp.read())
        nodetype, d = parsed
        assert nodetype == "dirnode"
        self.mutable = d.get("mutable", False) # older nodes don't provide it
        self.children_d = d["children"]
        self.children = None

    def populate(self, recurse):
        self.children = {}
        for i,(name, data) in enumerate(self.children_d):
            self.progressfunc("examining %d of %d" % (i, len(self.children_d)))
            if data[0] == "filenode":
                mutable = data[1].get("mutable", False)
                writecap = ascii_or_none(data[1].get("rw_uri"))
                readcap = ascii_or_none(data[1].get("ro_uri"))
                self.children[name] = TahoeFileSource(self.nodeurl, mutable,
                                                      writecap, readcap)
            else:
                assert data[0] == "dirnode"
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

class TahoeDirectoryTarget:
    def __init__(self, nodeurl, cache, progressfunc):
        self.nodeurl = nodeurl
        self.cache = cache
        self.progressfunc = progressfunc
        self.new_children = {}

    def init_from_grid(self, writecap, readcap):
        self.writecap = writecap
        self.readcap = readcap
        bestcap = writecap or readcap
        url = self.nodeurl + "uri/%s" % urllib.quote(bestcap)
        resp = do_http("GET", url + "?t=json")
        assert resp.status == 200
        parsed = simplejson.loads(resp.read())
        nodetype, d = parsed
        assert nodetype == "dirnode"
        self.mutable = d.get("mutable", False) # older nodes don't provide it
        self.children_d = d["children"]
        self.children = None

    def just_created(self, writecap):
        self.writecap = writecap
        self.readcap = uri.from_string().get_readonly().to_string()
        self.mutable = True
        self.children_d = {}
        self.children = {}

    def populate(self, recurse):
        self.children = {}
        for i,(name, data) in enumerate(self.children_d):
            self.progressfunc("examining %d of %d" % (i, len(self.children_d)))
            if data[0] == "filenode":
                mutable = data[1].get("mutable", False)
                writecap = ascii_or_none(data[1].get("rw_uri"))
                readcap = ascii_or_none(data[1].get("ro_uri"))
                self.children[name] = TahoeFileTarget(self.nodeurl, mutable,
                                                      writecap, readcap)
            else:
                assert data[0] == "dirnode"
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
        # I'm not sure this will work: we might not have .seek, so if not:
        #inf = inf.read()

        # TODO: this always creates immutable files. We might want an option
        # to always create mutable files, or to copy mutable files into new
        # mutable files.
        resp = do_http("PUT", url, inf)
        filecap = check_PUT(resp)
        self.new_children[name] = filecap

    def put_uri(self, name, filecap):
        self.new_children[name] = filecap

    def set_children(self):
        if not self.new_children:
            return
        # XXX TODO t=set_children

class Copier:
    def __init__(self, nodeurl, config, aliases,
                 verbosity, stdout, stderr,
                 progressfunc=None):
        if nodeurl[-1] != "/":
            nodeurl += "/"
        self.nodeurl = nodeurl
        self.progressfunc = progressfunc
        self.config = config
        self.aliases = aliases
        self.verbosity = verbosity
        self.stdout = stdout
        self.stderr = stderr

    def to_stderr(self, text):
        print >>self.stderr, text

    def do_copy(self, sources, destination):
        recursive = self.config["recursive"]

        #print "sources:", sources
        #print "dest:", destination

        target = self.get_info(destination)
        #print target

        source_info = dict([(self.get_info(source), source)
                            for source in sources])
        source_files = [s for s in source_info if s[0] == "file"]
        source_dirs = [s for s in source_info if s[0] == "directory"]
        empty_sources = [s for s in source_info if s[0] == "empty"]
        if empty_sources:
            for s in empty_sources:
                self.to_stderr("no such file or directory %s" % source_info[s])
            return 1

        #print "source_files", " ".join([source_info[s] for s in source_files])
        #print "source_dirs", " ".join([source_info[s] for s in source_dirs])

        if source_dirs and not recursive:
            self.to_stderr("cannot copy directories without --recursive")
            return 1

        if target[0] == "file":
            # cp STUFF foo.txt, where foo.txt already exists. This limits the
            # possibilities considerably.
            if len(sources) > 1:
                self.to_stderr("target '%s' is not a directory" % destination)
                return 1
            if source_dirs:
                self.to_stderr("cannot copy directory into a file")
                return 1
            return self.copy_to_file(source_files[0], target)

        if target[0] == "empty":
            if recursive:
                return self.copy_to_directory(source_files, source_dirs, target)
            if len(sources) > 1:
                # if we have -r, we'll auto-create the target directory. Without
                # it, we'll only create a file.
                self.to_stderr("cannot copy multiple files into a file without -r")
                return 1
            # cp file1 newfile
            return self.copy_to_file(source_files[0], target)

        if target[0] == "directory":
            return self.copy_to_directory(source_files, source_dirs, target)

        self.to_stderr("unknown target")
        return 1

    def get_info(self, target):
        rootcap, path = get_alias(self.aliases, target, None)
        if rootcap == DefaultAliasMarker:
            # this is a local file
            pathname = os.path.abspath(os.path.expanduser(path))
            if not os.path.exists(pathname):
                name = os.path.basename(pathname)
                return ("empty", "local", name, pathname)
            if os.path.isdir(pathname):
                return ("directory", "local", pathname)
            else:
                assert os.path.isfile(pathname)
                name = os.path.basename(pathname)
                return ("file", "local", name, pathname)
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
            return self.get_info_tahoe_dirnode(url, name)

    def get_info_tahoe_dirnode(self, url, name):
        resp = do_http("GET", url + "?t=json")
        if resp.status == 404:
            # doesn't exist yet
            return ("empty", "tahoe", False, name, None, None, url)
        parsed = simplejson.loads(resp.read())
        nodetype, d = parsed
        mutable = d.get("mutable", False) # older nodes don't provide 'mutable'
        rw_uri = ascii_or_none(d.get("rw_uri"))
        ro_uri = ascii_or_none(d.get("ro_uri"))
        if nodetype == "dirnode":
            return ("directory", "tahoe", mutable, name, rw_uri, ro_uri,
                    d["children"], url)
        else:
            return ("file", "tahoe", mutable, name, rw_uri, ro_uri, url)


    def get_file_data(self, source):
        assert source[0] == "file"
        if source[1] == "local":
            (ig1, ig2, name, pathname) = source
            return open(pathname, "rb").read()
        (ig1, ig2, mutable, name, writecap, readcap, url) = source
        return GET_to_string(url)

    def put_file_data(self, data, target):
        assert target[0] in ("file", "empty")
        if target[1] == "local":
            (ig1, ig2, name, pathname) = target
            open(pathname, "wb").write(data)
            return True
        (ig1, ig2, mutable, name, writecap, readcap, url) = target
        return PUT(url, data)

    def put_uri(self, uri, targeturl):
        return PUT(targeturl + "?t=uri", uri)

    def upload_data(self, data):
        url = self.nodeurl + "uri"
        return PUT(url, data)

    def copy_to_file(self, source, target):
        assert source[0] == "file"
        # do we need to copy bytes?
        if source[1] == "local" or source[2] == True or target[1] == "local":
            # yes
            data = self.get_file_data(source)
            self.put_file_data(data, target)
            return
        # no, we're getting data from an immutable source, and we're copying
        # into the tahoe grid, so we can just copy the URI.
        uri = source[3] or source[4] # prefer rw_uri, fall back to ro_uri
        # TODO: if the original was mutable, and we're creating the target,
        # should be we create a mutable file to match? At the moment we always
        # create immutable files.
        self.put_uri(uri, target[-1])

    def copy_to_directory(self, source_file_infos, source_dir_infos,
                          target_info):
        # step one: build a graph of the source tree. This returns a dictionary,
        # with child names as keys, and values that are either Directory or File
        # instances (local or tahoe).
        source_dirs = self.build_graphs(source_dir_infos)

        # step two: create the top-level target directory object
        assert target_info[0] in ("empty", "directory")
        if target_info[1] == "local":
            pathname = target_info[-1]
            if not os.path.exists(pathname):
                os.makedirs(pathname)
            assert os.path.isdir(pathname)
            target = LocalDirectoryTarget(self.progressfunc, target_info[-1])
        else:
            assert target_info[1] == "tahoe"
            target = TahoeDirectoryTarget(self.nodeurl, self.cache,
                                          self.progressfunc)
            if target_info[0] == "empty":
                writecap = mkdir(target_info[-1])
                target.just_created(writecap)
            else:
                (ig1, ig2, mutable, name, writecap, readcap, url) = target_info
                target.init_from_grid(writecap, readcap)

        # step three: find a target for each source node, creating
        # directories as necessary. 'targetmap' is a dictionary that uses
        # target Directory instances as keys, and has values of
        # (name->sourceobject) dicts for all the files that need to wind up
        # there.

        # sources are all LocalFile/LocalDirectory/TahoeFile/TahoeDirectory
        # target is LocalDirectory/TahoeDirectory

        self.targetmap = {}
        self.files_to_copy = 0

        for source in source_file_infos:
            if source[1] == "local":
                (ig1, ig2, name, pathname) = source
                s = LocalFileSource(pathname)
            else:
                assert source[1] == "tahoe"
                (ig1, ig2, mutable, name, writecap, readcap, url) = source
                s = TahoeFileSource(self.nodeurl, mutable,
                                    writecap, readcap)
            self.attach_to_target(s, name, target)
            self.files_to_copy += 1

        for source in source_dirs:
            self.assign_targets(source, target)

        self.progress("starting copy, %d files, %d directories" %
                      (self.files_to_copy, len(self.targets)))
        self.files_copied = 0
        self.targets_finished = 0

        # step four: walk through the list of targets. For each one, copy all
        # the files. If the target is a TahoeDirectory, upload and create
        # read-caps, then do a set_children to the target directory.

        for target in self.targets:
            self.copy_files(self.targets[target], target)
            self.targets_finished += 1
            self.progress("%d/%d directories" %
                          (self.targets_finished, len(self.targets)))

    def attach_to_target(self, source, name, target):
        if target not in self.targets:
            self.targets[target] = {}
        self.targets[target][name] = source
        self.files_to_copy += 1

    def assign_targets(self, source, target):
        # copy everything in s to the target
        assert isinstance(source, (LocalDirectorySource, TahoeDirectorySource))

        for name, child in source.children.items():
            if isinstance(child, (LocalDirectorySource, TahoeDirectorySource)):
                # we will need a target directory for this one
                subtarget = target.get_child_target(name)
                self.assign_targets(source, subtarget)
            else:
                assert isinstance(child, (LocalFileSource, TahoeFileSource))
                self.attach_to_target(source, name, target)



    def copy_files(self, targetmap, target):
        for name, source in targetmap.items():
            assert isinstance(source, (LocalFileSource, TahoeFileSource))
            self.copy_file(source, name, target)
            self.files_copied += 1
            self.progress("%d/%d files, %d/%d directories" %
                          (self.files_copied, self.files_to_copy,
                           self.targets_finished, len(self.targets)))
        target.set_children()

    def need_to_copy_bytes(self, source, target):
        if source.need_to_copy_bytes:
            # mutable tahoe files, and local files
            return True
        if isinstance(target, LocalDirectoryTarget):
            return True
        return False

    def copy_file(self, source, name, target):
        assert isinstance(source, (LocalFileSource, TahoeFileSource))
        if self.need_to_copy_bytes(source, target):
            # if the target is a local directory, this will just write the
            # bytes to disk. If it is a tahoe directory, it will upload the
            # data, and stash the new filecap for a later set_children call.
            f = source.open()
            target.put_file(name, f)
            return
        # otherwise we're copying tahoe to tahoe, and using immutable files,
        # so we can just make a link
        target.put_uri(name, source.bestcap())


    def progress(self, message):
        print message
        if self.progressfunc:
            self.progressfunc(message)

    def build_graphs(self, sources):
        cache = {}
        graphs = []
        for source in sources:
            assert source[0] == "directory"
            if source[1] == "local":
                root = LocalDirectorySource(self.progress, source[-1])
                root.populate(True)
            else:
                assert source[1] == "tahoe"
                (ig1, ig2, mutable, name, writecap, readcap, url) = source
                root = TahoeDirectorySource(self.nodeurl, cache, self.progress)
                root.init_from_grid(writecap, readcap)
                root.populate(True)
            graphs.append(root)
        return graphs


def copy(nodeurl, config, aliases, sources, destination,
         verbosity, stdout, stderr):
    c = Copier(nodeurl, config, aliases, verbosity, stdout, stderr)
    return c.do_copy(sources, destination)
