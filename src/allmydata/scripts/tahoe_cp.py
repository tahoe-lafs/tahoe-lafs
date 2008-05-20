
import os.path
import urllib
import simplejson
from allmydata.scripts.common import get_alias, escape_path, DefaultAliasMarker
from allmydata.scripts.common_http import do_http

def ascii_or_none(s):
    if s is None:
        return s
    return str(s)

def get_info(nodeurl, aliases, target):
    rootcap, path = get_alias(aliases, target, None)
    if rootcap == DefaultAliasMarker:
        # this is a local file
        pathname = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(pathname):
            return ("empty", "local", pathname)
        if os.path.isdir(pathname):
            return ("directory", "local", pathname)
        else:
            assert os.path.isfile(pathname)
            return ("file", "local", pathname)
    else:
        # this is a tahoe object
        url = nodeurl + "uri/%s" % urllib.quote(rootcap)
        if path:
            url += "/" + escape_path(path)
        resp = do_http("GET", url + "?t=json")
        if resp.status == 404:
            # doesn't exist yet
            return ("empty", "tahoe", False, None, None, url)
        parsed = simplejson.loads(resp.read())
        nodetype, d = parsed
        mutable = d.get("mutable", False) # older nodes don't provide 'mutable'
        rw_uri = ascii_or_none(d.get("rw_uri"))
        ro_uri = ascii_or_none(d.get("ro_uri"))
        if nodetype == "dirnode":
            return ("directory", "tahoe", mutable, rw_uri, ro_uri, url)
        else:
            return ("file", "tahoe", mutable, rw_uri, ro_uri, url)

def copy(nodeurl, config, aliases, sources, destination,
         verbosity, stdout, stderr):
    if nodeurl[-1] != "/":
        nodeurl += "/"
    recursive = config["recursive"]

    #print "sources:", sources
    #print "dest:", destination

    target = get_info(nodeurl, aliases, destination)
    #print target

    source_info = dict([(get_info(nodeurl, aliases, source), source)
                        for source in sources])
    source_files = [s for s in source_info if s[0] == "file"]
    source_dirs = [s for s in source_info if s[0] == "directory"]
    empty_sources = [s for s in source_info if s[0] == "empty"]
    if empty_sources:
        for s in empty_sources:
            print >>stderr, "no such file or directory %s" % source_info[s]
        return 1

    #print "source_files", " ".join([source_info[s] for s in source_files])
    #print "source_dirs", " ".join([source_info[s] for s in source_dirs])

    if source_dirs and not recursive:
        print >>stderr, "cannot copy directories without --recursive"
        return 1

    if target[0] == "file":
        # cp STUFF foo.txt, where foo.txt already exists. This limits the
        # possibilities considerably.
        if len(sources) > 1:
            print >>stderr, "target '%s' is not a directory" % destination
            return 1
        if source_dirs:
            print >>stderr, "cannot copy directory into a file"
            return 1
        return copy_to_file(source_files[0], target)

    if target[0] == "empty":
        if recursive:
            return copy_to_directory(source_files, source_dirs, target)
        if len(sources) > 1:
            # if we have -r, we'll auto-create the target directory. Without
            # it, we'll only create a file.
            print >>stderr, "cannot copy multiple files into a file without -r"
            return 1
        # cp file1 newfile
        return copy_to_file(source_files[0], target)

    if target[0] == "directory":
        return copy_to_directory(source_files, source_dirs, target)

    print >>stderr, "unknown target"
    return 1


def get_file_data(source):
    assert source[0] == "file"
    if source[1] == "local":
        return open(source[2], "rb").read()
    return do_http("GET", source[-1]).read()

class WriteError(Exception):
    pass

def check_PUT(resp):
    if resp.status in (200, 201):
        return True
    raise WriteError("Error during PUT: %s %s %s" % (resp.status, resp.reason,
                                                     resp.read()))

def put_file_data(data, target):
    if target[1] == "local":
        open(target[2], "wb").write(data)
        return True
    resp = do_http("PUT", target[-1], data)
    return check_PUT(resp)

def put_uri(uri, target):
    resp = do_http("PUT", target[-1] + "?t=uri", uri)
    return check_PUT(resp)

def copy_to_file(source, target):
    assert source[0] == "file"
    # do we need to copy bytes?
    if source[1] == "local" or source[2] == True or target[1] == "local":
        # yes
        data = get_file_data(source)
        put_file_data(data, target)
        return
    # no, we're getting data from an immutable source, and we're copying into
    # the tahoe grid, so we can just copy the URI.
    uri = source[3] or source[4] # prefer rw_uri, fall back to ro_uri
    # TODO: if the original was mutable, and we're creating the target,
    # should be we create a mutable file to match? At the moment we always
    # create immutable files.
    put_uri(uri, target)

def copy_to_directory(source_files, source_dirs, target):
    NotImplementedError
