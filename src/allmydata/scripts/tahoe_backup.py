
import os.path
import time
import urllib
import simplejson
import datetime
from allmydata.scripts.common import get_alias, escape_path, DEFAULT_ALIAS, \
                                     UnknownAliasError
from allmydata.scripts.common_http import do_http, HTTPError, format_http_error
from allmydata.util import time_format
from allmydata.scripts import backupdb
from allmydata.util.stringutils import listdir_unicode, open_unicode, quote_output, to_str
from allmydata.util.assertutil import precondition


def get_local_metadata(path):
    metadata = {}

    # posix stat(2) metadata, depends on the platform
    os.stat_float_times(True)
    s = os.stat(path)
    metadata["ctime"] = s.st_ctime
    metadata["mtime"] = s.st_mtime

    misc_fields = ("st_mode", "st_ino", "st_dev", "st_uid", "st_gid")
    macos_misc_fields = ("st_rsize", "st_creator", "st_type")
    for field in misc_fields + macos_misc_fields:
        if hasattr(s, field):
            metadata[field] = getattr(s, field)

    # TODO: extended attributes, like on OS-X's HFS+
    return metadata

def mkdir(contents, options):
    kids = dict([ (childname, (contents[childname][0],
                               {"ro_uri": contents[childname][1],
                                "metadata": contents[childname][2],
                                }))
                  for childname in contents
                  ])
    body = simplejson.dumps(kids).encode("utf-8")
    url = options['node-url'] + "uri?t=mkdir-immutable"
    resp = do_http("POST", url, body)
    if resp.status < 200 or resp.status >= 300:
        raise HTTPError("Error during mkdir", resp)

    dircap = to_str(resp.read().strip())
    return dircap

def put_child(dirurl, childname, childcap):
    assert dirurl[-1] == "/"
    url = dirurl + urllib.quote(childname) + "?t=uri"
    resp = do_http("PUT", url, childcap)
    if resp.status not in (200, 201):
        raise HTTPError("Error during put_child", resp)

class BackupProcessingError(Exception):
    pass

class BackerUpper:
    def __init__(self, options):
        self.options = options
        self.files_uploaded = 0
        self.files_reused = 0
        self.files_checked = 0
        self.files_skipped = 0
        self.directories_created = 0
        self.directories_reused = 0
        self.directories_checked = 0
        self.directories_skipped = 0

    def run(self):
        options = self.options
        nodeurl = options['node-url']
        self.verbosity = 1
        if options['quiet']:
            self.verbosity = 0
        if options['verbose']:
            self.verbosity = 2
        stdout = options.stdout
        stderr = options.stderr

        start_timestamp = datetime.datetime.now()
        self.backupdb = None
        bdbfile = os.path.join(options["node-directory"],
                               "private", "backupdb.sqlite")
        bdbfile = os.path.abspath(bdbfile)
        self.backupdb = backupdb.get_backupdb(bdbfile, stderr)
        if not self.backupdb:
            print >>stderr, "ERROR: Unable to load backup db."
            return 1

        try:
            rootcap, path = get_alias(options.aliases, options.to_dir, DEFAULT_ALIAS)
        except UnknownAliasError, e:
            e.display(stderr)
            return 1
        to_url = nodeurl + "uri/%s/" % urllib.quote(rootcap)
        if path:
            to_url += escape_path(path)
        if not to_url.endswith("/"):
            to_url += "/"

        archives_url = to_url + "Archives/"

        # first step: make sure the target directory exists, as well as the
        # Archives/ subdirectory.
        resp = do_http("GET", archives_url + "?t=json")
        if resp.status == 404:
            resp = do_http("POST", archives_url + "?t=mkdir")
            if resp.status != 200:
                print >>stderr, format_http_error("Unable to create target directory", resp)
                return 1

        # second step: process the tree
        new_backup_dircap = self.process(options.from_dir)

        # third: attach the new backup to the list
        now = time_format.iso_utc(int(time.time()), sep="_") + "Z"

        put_child(archives_url, now, new_backup_dircap)
        put_child(to_url, "Latest", new_backup_dircap)
        end_timestamp = datetime.datetime.now()
        # calc elapsed time, omitting microseconds
        elapsed_time = str(end_timestamp - start_timestamp).split('.')[0]

        if self.verbosity >= 1:
            print >>stdout, (" %d files uploaded (%d reused), "
                             "%d files skipped, "
                             "%d directories created (%d reused), "
                             "%d directories skipped"
                             % (self.files_uploaded,
                                self.files_reused,
                                self.files_skipped,
                                self.directories_created,
                                self.directories_reused,
                                self.directories_skipped))
            if self.verbosity >= 2:
                print >>stdout, (" %d files checked, %d directories checked"
                                 % (self.files_checked,
                                    self.directories_checked))
            print >>stdout, " backup done, elapsed time: %s" % elapsed_time

        # The command exits with code 2 if files or directories were skipped
        if self.files_skipped or self.directories_skipped:
            return 2

        # done!
        return 0

    def verboseprint(self, msg):
        precondition(isinstance(msg, str), msg)
        if self.verbosity >= 2:
            print >>self.options.stdout, msg

    def warn(self, msg):
        precondition(isinstance(msg, str), msg)
        print >>self.options.stderr, msg

    def process(self, localpath):
        precondition(isinstance(localpath, unicode), localpath)
        # returns newdircap

        self.verboseprint("processing %s" % quote_output(localpath))
        create_contents = {} # childname -> (type, rocap, metadata)
        compare_contents = {} # childname -> rocap

        try:
            children = listdir_unicode(localpath)
        except EnvironmentError:
            self.directories_skipped += 1
            self.warn("WARNING: permission denied on directory %s" % quote_output(localpath))
            children = []

        for child in self.options.filter_listdir(children):
            assert isinstance(child, unicode), child
            childpath = os.path.join(localpath, child)
            # note: symlinks to directories are both islink() and isdir()
            if os.path.isdir(childpath) and not os.path.islink(childpath):
                metadata = get_local_metadata(childpath)
                # recurse on the child directory
                childcap = self.process(childpath)
                assert isinstance(childcap, str)
                create_contents[child] = ("dirnode", childcap, metadata)
                compare_contents[child] = childcap
            elif os.path.isfile(childpath) and not os.path.islink(childpath):
                try:
                    childcap, metadata = self.upload(childpath)
                    assert isinstance(childcap, str)
                    create_contents[child] = ("filenode", childcap, metadata)
                    compare_contents[child] = childcap
                except EnvironmentError:
                    self.files_skipped += 1
                    self.warn("WARNING: permission denied on file %s" % quote_output(childpath))
            else:
                self.files_skipped += 1
                if os.path.islink(childpath):
                    self.warn("WARNING: cannot backup symlink %s" % quote_output(childpath))
                else:
                    self.warn("WARNING: cannot backup special file %s" % quote_output(childpath))

        must_create, r = self.check_backupdb_directory(compare_contents)
        if must_create:
            self.verboseprint(" creating directory for %s" % quote_output(localpath))
            newdircap = mkdir(create_contents, self.options)
            assert isinstance(newdircap, str)
            if r:
                r.did_create(newdircap)
            self.directories_created += 1
            return newdircap
        else:
            self.verboseprint(" re-using old directory for %s" % quote_output(localpath))
            self.directories_reused += 1
            return r.was_created()

    def check_backupdb_file(self, childpath):
        if not self.backupdb:
            return True, None
        use_timestamps = not self.options["ignore-timestamps"]
        r = self.backupdb.check_file(childpath, use_timestamps)

        if not r.was_uploaded():
            return True, r

        if not r.should_check():
            # the file was uploaded or checked recently, so we can just use
            # it
            return False, r

        # we must check the file before using the results
        filecap = r.was_uploaded()
        self.verboseprint("checking %s" % quote_output(filecap))
        nodeurl = self.options['node-url']
        checkurl = nodeurl + "uri/%s?t=check&output=JSON" % urllib.quote(filecap)
        self.files_checked += 1
        resp = do_http("POST", checkurl)
        if resp.status != 200:
            # can't check, so we must assume it's bad
            return True, r

        cr = simplejson.loads(resp.read())
        healthy = cr["results"]["healthy"]
        if not healthy:
            # must upload
            return True, r
        # file is healthy, no need to upload
        r.did_check_healthy(cr)
        return False, r

    def check_backupdb_directory(self, compare_contents):
        if not self.backupdb:
            return True, None
        r = self.backupdb.check_directory(compare_contents)

        if not r.was_created():
            return True, r

        if not r.should_check():
            # the file was uploaded or checked recently, so we can just use
            # it
            return False, r

        # we must check the directory before re-using it
        dircap = r.was_created()
        self.verboseprint("checking %s" % quote_output(dircap))
        nodeurl = self.options['node-url']
        checkurl = nodeurl + "uri/%s?t=check&output=JSON" % urllib.quote(dircap)
        self.directories_checked += 1
        resp = do_http("POST", checkurl)
        if resp.status != 200:
            # can't check, so we must assume it's bad
            return True, r

        cr = simplejson.loads(resp.read())
        healthy = cr["results"]["healthy"]
        if not healthy:
            # must create
            return True, r
        # directory is healthy, no need to upload
        r.did_check_healthy(cr)
        return False, r

    # This function will raise an IOError exception when called on an unreadable file
    def upload(self, childpath):
        precondition(isinstance(childpath, unicode), childpath)

        #self.verboseprint("uploading %s.." % quote_output(childpath))
        metadata = get_local_metadata(childpath)

        # we can use the backupdb here
        must_upload, bdb_results = self.check_backupdb_file(childpath)

        if must_upload:
            self.verboseprint("uploading %s.." % quote_output(childpath))
            infileobj = open_unicode(childpath, "rb")
            url = self.options['node-url'] + "uri"
            resp = do_http("PUT", url, infileobj)
            if resp.status not in (200, 201):
                raise HTTPError("Error during file PUT", resp)

            filecap = resp.read().strip()
            self.verboseprint(" %s -> %s" % (quote_output(childpath, quotemarks=False),
                                             quote_output(filecap, quotemarks=False)))
            #self.verboseprint(" metadata: %s" % (quote_output(metadata, quotemarks=False),))

            if bdb_results:
                bdb_results.did_upload(filecap)

            self.files_uploaded += 1
            return filecap, metadata

        else:
            self.verboseprint("skipping %s.." % quote_output(childpath))
            self.files_reused += 1
            return bdb_results.was_uploaded(), metadata

def backup(options):
    bu = BackerUpper(options)
    return bu.run()
