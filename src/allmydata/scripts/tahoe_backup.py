
import os.path
import time
import urllib
import simplejson
import datetime
from allmydata.scripts.common import get_alias, escape_path, DEFAULT_ALIAS
from allmydata.scripts.common_http import do_http
from allmydata import uri
from allmydata.util import time_format
from allmydata.scripts import backupdb

class HTTPError(Exception):
    pass

def raiseHTTPError(msg, resp):
    msg = msg + ": %s %s %s" % (resp.status, resp.reason, resp.read())
    raise HTTPError(msg)

def readonly(writedircap):
    return uri.from_string_dirnode(writedircap).get_readonly().to_string()

def parse_old_timestamp(s, options):
    try:
        if not s.endswith("Z"):
            raise ValueError
        # This returns seconds-since-epoch for an ISO-8601-ish-formatted UTC
        # time string. This might raise ValueError if the string is not in the
        # right format.
        when = time_format.iso_utc_time_to_seconds(s[:-1])
        return when
    except ValueError:
        pass

    try:
        # "2008-11-16 10.34 PM" (localtime)
        if s[-3:] in (" AM", " PM"):
            # this might raise ValueError
            when = time.strptime(s[:-3], "%Y-%m-%d %I.%M")
            if s[-3:] == "PM":
                when += 12*60*60
            return when
    except ValueError:
        pass

    try:
        # "2008-11-16 10.34.56 PM" (localtime)
        if s[-3:] in (" AM", " PM"):
            # this might raise ValueError
            when = time.strptime(s[:-3], "%Y-%m-%d %I.%M.%S")
            if s[-3:] == "PM":
                when += 12*60*60
            return when
    except ValueError:
        pass

    try:
        # "2008-12-31 18.21.43"
        when = time.strptime(s, "%Y-%m-%d %H.%M.%S")
        return when
    except ValueError:
        pass

    print >>options.stderr, "unable to parse old timestamp '%s', ignoring" % s
    return None

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
    url = options['node-url'] + "uri?t=mkdir"
    resp = do_http("POST", url)
    if resp.status < 200 or resp.status >= 300:
        raiseHTTPError("error during mkdir", resp)
    dircap = str(resp.read().strip())
    url = options['node-url'] + "uri/%s?t=set_children" % urllib.quote(dircap)
    body = dict([ (childname, (contents[childname][0],
                               {"ro_uri": contents[childname][1],
                                "metadata": contents[childname][2],
                                }))
                  for childname in contents
                  ])
    resp = do_http("POST", url, simplejson.dumps(body))
    if resp.status != 200:
        raiseHTTPError("error during set_children", resp)
    return dircap

def put_child(dirurl, childname, childcap):
    assert dirurl[-1] == "/"
    url = dirurl + urllib.quote(childname) + "?t=uri"
    resp = do_http("PUT", url, childcap)
    if resp.status not in (200, 201):
        raiseHTTPError("error during put_child", resp)

def directory_is_changed(a, b):
    # each is a mapping from childname to (type, cap, metadata)
    significant_metadata = ("ctime", "mtime")
    # other metadata keys are preserved, but changes to them won't trigger a
    # new backup

    if set(a.keys()) != set(b.keys()):
        return True
    for childname in a:
        a_type, a_cap, a_metadata = a[childname]
        b_type, b_cap, b_metadata = b[childname]
        if a_type != b_type:
            return True
        if a_cap != b_cap:
            return True
        for k in significant_metadata:
            if a_metadata.get(k) != b_metadata.get(k):
                return True
    return False

class BackupProcessingError(Exception):
    pass

class BackerUpper:
    def __init__(self, options):
        self.options = options
        self.files_uploaded = 0
        self.files_reused = 0
        self.files_checked = 0
        self.directories_read = 0
        self.directories_created = 0
        self.directories_reused = 0
        self.directories_checked = 0

    def run(self):
        options = self.options
        nodeurl = options['node-url']
        from_dir = options.from_dir
        to_dir = options.to_dir
        self.verbosity = 1
        if options['quiet']:
            self.verbosity = 0
        if options['verbose']:
            self.verbosity = 2
        stdin = options.stdin
        stdout = options.stdout
        stderr = options.stderr

        start_timestamp = datetime.datetime.now()
        self.backupdb = None
        use_backupdb = not options["no-backupdb"]
        if use_backupdb:
            bdbfile = os.path.join(options["node-directory"],
                                   "private", "backupdb.sqlite")
            bdbfile = os.path.abspath(bdbfile)
            self.backupdb = backupdb.get_backupdb(bdbfile, stderr)
            if not self.backupdb:
                # get_backupdb() has already delivered a lengthy speech about
                # where to find pysqlite and how to add --no-backupdb
                print >>stderr, "ERROR: Unable to import sqlite."
                return 1

        rootcap, path = get_alias(options.aliases, options.to_dir, DEFAULT_ALIAS)
        to_url = nodeurl + "uri/%s/" % urllib.quote(rootcap)
        if path:
            to_url += escape_path(path)
        if not to_url.endswith("/"):
            to_url += "/"

        archives_url = to_url + "Archives/"
        latest_url = to_url + "Latest"

        # first step: make sure the target directory exists, as well as the
        # Archives/ subdirectory.
        resp = do_http("GET", archives_url + "?t=json")
        if resp.status == 404:
            resp = do_http("POST", archives_url + "?t=mkdir")
            if resp.status != 200:
                print >>stderr, "Unable to create target directory: %s %s %s" % \
                      (resp.status, resp.reason, resp.read())
                return 1
            archives_dir = {}
        else:
            jdata = simplejson.load(resp)
            (otype, attrs) = jdata
            archives_dir = attrs["children"]

        # second step: locate the most recent backup in TODIR/Archives/*
        latest_backup_time = 0
        latest_backup_name = None
        latest_backup_dircap = None

        # we have various time formats. The allmydata.com windows backup tool
        # appears to create things like "2008-11-16 10.34 PM". This script
        # creates things like "2008-11-16--17.34Z".
        for archive_name in archives_dir.keys():
            if archives_dir[archive_name][0] != "dirnode":
                continue
            when = parse_old_timestamp(archive_name, options)
            if when is not None:
                if when > latest_backup_time:
                    latest_backup_time = when
                    latest_backup_name = archive_name
                    latest_backup_dircap = str(archives_dir[archive_name][1]["ro_uri"])

        # third step: process the tree
        new_backup_dircap = self.process(options.from_dir, latest_backup_dircap)

        # fourth: attach the new backup to the list
        new_readonly_backup_dircap = readonly(new_backup_dircap)
        now = time_format.iso_utc(int(time.time()), sep="_") + "Z"

        put_child(archives_url, now, new_readonly_backup_dircap)
        put_child(to_url, "Latest", new_readonly_backup_dircap)
        end_timestamp = datetime.datetime.now()
        # calc elapsed time, omitting microseconds
        elapsed_time = str(end_timestamp - start_timestamp).split('.')[0]

        if self.verbosity >= 1:
            print >>stdout, (" %d files uploaded (%d reused), "
                             "%d directories created (%d reused)"
                             % (self.files_uploaded,
                                self.files_reused,
                                self.directories_created,
                                self.directories_reused))
            if self.verbosity >= 2:
                print >>stdout, (" %d files checked, %d directories checked, "
                                 "%d directories read"
                                 % (self.files_checked,
                                    self.directories_checked,
                                    self.directories_read))
            print >>stdout, " backup done, elapsed time: %s" % elapsed_time
        # done!
        return 0

    def verboseprint(self, msg):
        if self.verbosity >= 2:
            print >>self.options.stdout, msg

    def process(self, localpath, olddircap):
        # returns newdircap

        self.verboseprint("processing %s, olddircap %s" % (localpath, olddircap))
        olddircontents = {}
        if olddircap:
            olddircontents = self.readdir(olddircap)

        newdircontents = {} # childname -> (type, rocap, metadata)
        for child in self.options.filter_listdir(os.listdir(localpath)):
            childpath = os.path.join(localpath, child)
            if os.path.isdir(childpath):
                metadata = get_local_metadata(childpath)
                oldchildcap = None
                if olddircontents is not None and child in olddircontents:
                    oldchildcap = olddircontents[child][1]
                # recurse on the child directory
                newchilddircap = self.process(childpath, oldchildcap)
                newdircontents[child] = ("dirnode", newchilddircap, metadata)
            elif os.path.isfile(childpath):
                newfilecap, metadata = self.upload(childpath)
                newdircontents[child] = ("filenode", newfilecap, metadata)
            else:
                raise BackupProcessingError("Cannot backup this file %r" % childpath)

        if (olddircap
            and olddircontents is not None
            and not directory_is_changed(newdircontents, olddircontents)
            ):
            self.verboseprint(" %s not changed, re-using old directory" % localpath)
            # yay! they're identical!
            self.directories_reused += 1
            return olddircap
        else:
            self.verboseprint(" %s changed, making new directory" % localpath)
            # something changed, or there was no previous directory, so we
            # must make a new directory
            newdircap = mkdir(newdircontents, self.options)
            self.directories_created += 1
            return readonly(newdircap)

    def check_backupdb(self, childpath):
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
        self.verboseprint("checking %s" % filecap)
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

    def readdir(self, dircap):
        # returns a dict of (childname: (type, readcap, metadata)), or None
        # if the dircap didn't point to a directory
        self.directories_read += 1
        url = self.options['node-url'] + "uri/%s?t=json" % urllib.quote(dircap)
        resp = do_http("GET", url)
        if resp.status != 200:
            raiseHTTPError("Error during directory GET", resp)
        jd = simplejson.load(resp)
        ntype, ndata = jd
        if ntype != "dirnode":
            return None
        contents = {}
        for (childname, (childtype, childdata)) in ndata["children"].items():
            contents[childname] = (childtype,
                                   str(childdata["ro_uri"]),
                                   childdata["metadata"])
        return contents

    def upload(self, childpath):
        #self.verboseprint("uploading %s.." % childpath)
        metadata = get_local_metadata(childpath)

        # we can use the backupdb here
        must_upload, bdb_results = self.check_backupdb(childpath)

        if must_upload:
            self.verboseprint("uploading %s.." % childpath)
            infileobj = open(os.path.expanduser(childpath), "rb")
            url = self.options['node-url'] + "uri"
            resp = do_http("PUT", url, infileobj)
            if resp.status not in (200, 201):
                raiseHTTPError("Error during file PUT", resp)
            filecap = resp.read().strip()
            self.verboseprint(" %s -> %s" % (childpath, filecap))
            #self.verboseprint(" metadata: %s" % (metadata,))

            if bdb_results:
                bdb_results.did_upload(filecap)

            self.files_uploaded += 1
            return filecap, metadata

        else:
            self.verboseprint("skipping %s.." % childpath)
            self.files_reused += 1
            return bdb_results.was_uploaded(), metadata

def backup(options):
    bu = BackerUpper(options)
    return bu.run()
