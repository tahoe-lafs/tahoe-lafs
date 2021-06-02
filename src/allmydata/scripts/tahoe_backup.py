"""
Ported to Python 3.
"""
from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os.path
import time
from urllib.parse import quote as url_quote
import datetime

from allmydata.scripts.common import get_alias, escape_path, DEFAULT_ALIAS, \
                                     UnknownAliasError
from allmydata.scripts.common_http import do_http, HTTPError, format_http_error
from allmydata.util import time_format, jsonbytes as json
from allmydata.scripts import backupdb
from allmydata.util.encodingutil import listdir_unicode, quote_output, \
     quote_local_unicode_path, to_bytes, FilenameEncodingError, unicode_to_url
from allmydata.util.assertutil import precondition
from allmydata.util.fileutil import abspath_expanduser_unicode, precondition_abspath


def get_local_metadata(path):
    metadata = {}

    # posix stat(2) metadata, depends on the platform
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
    body = json.dumps(kids).encode("utf-8")
    url = options['node-url'] + "uri?t=mkdir-immutable"
    resp = do_http("POST", url, body)
    if resp.status < 200 or resp.status >= 300:
        raise HTTPError("Error during mkdir", resp)

    dircap = to_bytes(resp.read().strip())
    return dircap

def put_child(dirurl, childname, childcap):
    assert dirurl[-1] != "/"
    url = dirurl + "/" + url_quote(unicode_to_url(childname)) + "?t=uri"
    resp = do_http("PUT", url, childcap)
    if resp.status not in (200, 201):
        raise HTTPError("Error during put_child", resp)

class BackerUpper(object):
    """
    :ivar int _files_checked: The number of files which the backup process has
        so-far inspected on the grid to determine if they need to be
        re-uploaded.

    :ivar int _directories_checked: The number of directories which the backup
        process has so-far inspected on the grid to determine if they need to
        be re-uploaded.
    """
    def __init__(self, options):
        self.options = options
        self._files_checked = 0
        self._directories_checked = 0

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
        bdbfile = os.path.join(options["node-directory"],
                               "private", "backupdb.sqlite")
        bdbfile = abspath_expanduser_unicode(bdbfile)
        self.backupdb = backupdb.get_backupdb(bdbfile, stderr)
        if not self.backupdb:
            print("ERROR: Unable to load backup db.", file=stderr)
            return 1

        try:
            rootcap, path = get_alias(options.aliases, options.to_dir, DEFAULT_ALIAS)
        except UnknownAliasError as e:
            e.display(stderr)
            return 1
        to_url = nodeurl + "uri/%s/" % url_quote(rootcap)
        if path:
            to_url += escape_path(path)
        if not to_url.endswith("/"):
            to_url += "/"

        archives_url = to_url + "Archives/"

        archives_url = archives_url.rstrip("/")
        to_url = to_url.rstrip("/")

        # first step: make sure the target directory exists, as well as the
        # Archives/ subdirectory.
        resp = do_http("GET", archives_url + "?t=json")
        if resp.status == 404:
            resp = do_http("POST", archives_url + "?t=mkdir")
            if resp.status != 200:
                print(format_http_error("Unable to create target directory", resp), file=stderr)
                return 1

        # second step: process the tree
        targets = list(collect_backup_targets(
            options.from_dir,
            listdir_unicode,
            self.options.filter_listdir,
        ))
        completed = run_backup(
            warn=self.warn,
            upload_file=self.upload,
            upload_directory=self.upload_directory,
            targets=targets,
            start_timestamp=start_timestamp,
            stdout=stdout,
        )
        new_backup_dircap = completed.dircap

        # third: attach the new backup to the list
        now = time_format.iso_utc(int(time.time()), sep="_") + "Z"

        put_child(archives_url, now, new_backup_dircap)
        put_child(to_url, "Latest", new_backup_dircap)
        print(completed.report(
            self.verbosity,
            self._files_checked,
            self._directories_checked,
        ), file=stdout)

        # The command exits with code 2 if files or directories were skipped
        if completed.any_skips():
            return 2

        # done!
        return 0

    def verboseprint(self, msg):
        precondition(isinstance(msg, str), msg)
        if self.verbosity >= 2:
            print(msg, file=self.options.stdout)

    def warn(self, msg):
        precondition(isinstance(msg, str), msg)
        print(msg, file=self.options.stderr)

    def upload_directory(self, path, compare_contents, create_contents):
        must_create, r = self.check_backupdb_directory(compare_contents)
        if must_create:
            self.verboseprint(" creating directory for %s" % quote_local_unicode_path(path))
            newdircap = mkdir(create_contents, self.options)
            assert isinstance(newdircap, bytes)
            if r:
                r.did_create(newdircap)
            return True, newdircap
        else:
            self.verboseprint(" re-using old directory for %s" % quote_local_unicode_path(path))
            return False, r.was_created()


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
        checkurl = nodeurl + "uri/%s?t=check&output=JSON" % url_quote(filecap)
        self._files_checked += 1
        resp = do_http("POST", checkurl)
        if resp.status != 200:
            # can't check, so we must assume it's bad
            return True, r

        cr = json.loads(resp.read())
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
        checkurl = nodeurl + "uri/%s?t=check&output=JSON" % url_quote(dircap)
        self._directories_checked += 1
        resp = do_http("POST", checkurl)
        if resp.status != 200:
            # can't check, so we must assume it's bad
            return True, r

        cr = json.loads(resp.read())
        healthy = cr["results"]["healthy"]
        if not healthy:
            # must create
            return True, r
        # directory is healthy, no need to upload
        r.did_check_healthy(cr)
        return False, r

    # This function will raise an IOError exception when called on an unreadable file
    def upload(self, childpath):
        precondition_abspath(childpath)

        #self.verboseprint("uploading %s.." % quote_local_unicode_path(childpath))
        metadata = get_local_metadata(childpath)

        # we can use the backupdb here
        must_upload, bdb_results = self.check_backupdb_file(childpath)

        if must_upload:
            self.verboseprint("uploading %s.." % quote_local_unicode_path(childpath))
            infileobj = open(childpath, "rb")
            url = self.options['node-url'] + "uri"
            resp = do_http("PUT", url, infileobj)
            if resp.status not in (200, 201):
                raise HTTPError("Error during file PUT", resp)

            filecap = resp.read().strip()
            self.verboseprint(" %s -> %s" % (quote_local_unicode_path(childpath, quotemarks=False),
                                             quote_output(filecap, quotemarks=False)))
            #self.verboseprint(" metadata: %s" % (quote_output(metadata, quotemarks=False),))

            if bdb_results:
                bdb_results.did_upload(filecap)

            return True, filecap, metadata

        else:
            self.verboseprint("skipping %s.." % quote_local_unicode_path(childpath))
            return False, bdb_results.was_uploaded(), metadata


def backup(options):
    bu = BackerUpper(options)
    return bu.run()


def collect_backup_targets(root, listdir, filter_children):
    """
    Yield BackupTargets in a suitable order for processing (deepest targets
    before their parents).
    """
    try:
        children = listdir(root)
    except EnvironmentError:
        yield PermissionDeniedTarget(root, isdir=True)
    except FilenameEncodingError:
        yield FilenameUndecodableTarget(root, isdir=True)
    else:
        for child in filter_children(children):
            assert isinstance(child, str), child
            childpath = os.path.join(root, child)
            if os.path.islink(childpath):
                yield LinkTarget(childpath, isdir=False)
            elif os.path.isdir(childpath):
                child_targets = collect_backup_targets(
                    childpath,
                    listdir,
                    filter_children,
                )
                for child_target in child_targets:
                    yield child_target
            elif os.path.isfile(childpath):
                yield FileTarget(childpath)
            else:
                yield SpecialTarget(childpath)
        yield DirectoryTarget(root)


def run_backup(
        warn,
        upload_file,
        upload_directory,
        targets,
        start_timestamp,
        stdout,
):
    progress = BackupProgress(warn, start_timestamp, len(targets))
    for target in targets:
        # Pass in the progress and get back a progress.  It would be great if
        # progress objects were immutable.  Then the target's backup would
        # make a new progress with the desired changes and return it to us.
        # Currently, BackupProgress is mutable, though, and everything just
        # mutates it.
        progress = target.backup(progress, upload_file, upload_directory)
        print(progress.report(datetime.datetime.now()), file=stdout)
    return progress.backup_finished()


class FileTarget(object):
    def __init__(self, path):
        self._path = path

    def __repr__(self):
        return "<File {}>".format(self._path)

    def backup(self, progress, upload_file, upload_directory):
        try:
            created, childcap, metadata = upload_file(self._path)
        except EnvironmentError:
            target = PermissionDeniedTarget(self._path, isdir=False)
            return target.backup(progress, upload_file, upload_directory)
        else:
            assert isinstance(childcap, bytes)
            if created:
                return progress.created_file(self._path, childcap, metadata)
            return progress.reused_file(self._path, childcap, metadata)


class DirectoryTarget(object):
    def __init__(self, path):
        self._path = path

    def __repr__(self):
        return "<Directory {}>".format(self._path)

    def backup(self, progress, upload_file, upload_directory):
        metadata = get_local_metadata(self._path)
        progress, create, compare = progress.consume_directory(self._path)
        did_create, dircap = upload_directory(self._path, compare, create)
        if did_create:
            return progress.created_directory(self._path, dircap, metadata)
        return progress.reused_directory(self._path, dircap, metadata)


class _ErrorTarget(object):
    def __init__(self, path, isdir=False):
        self._path = path
        self._quoted_path = quote_local_unicode_path(path)
        self._isdir = isdir


class PermissionDeniedTarget(_ErrorTarget):
    def backup(self, progress, upload_file, upload_directory):
        return progress.permission_denied(self._isdir, self._quoted_path)


class FilenameUndecodableTarget(_ErrorTarget):
    def backup(self, progress, upload_file, upload_directory):
        return progress.decoding_failed(self._isdir, self._quoted_path)


class LinkTarget(_ErrorTarget):
    def backup(self, progress, upload_file, upload_directory):
        return progress.unsupported_filetype(
            self._isdir,
            self._quoted_path,
            "symlink",
        )


class SpecialTarget(_ErrorTarget):
    def backup(self, progress, upload_file, upload_directory):
        return progress.unsupported_filetype(
            self._isdir,
            self._quoted_path,
            "special",
        )


class BackupComplete(object):
    def __init__(self,
                 start_timestamp,
                 end_timestamp,
                 files_created,
                 files_reused,
                 files_skipped,
                 directories_created,
                 directories_reused,
                 directories_skipped,
                 dircap,
    ):
        self._start_timestamp = start_timestamp
        self._end_timestamp = end_timestamp
        self._files_created = files_created
        self._files_reused = files_reused
        self._files_skipped = files_skipped
        self._directories_created = directories_created
        self._directories_reused = directories_reused
        self._directories_skipped = directories_skipped
        self.dircap = dircap

    def any_skips(self):
        return self._files_skipped or self._directories_skipped

    def report(self, verbosity, files_checked, directories_checked):
        result = []

        if verbosity >= 1:
            result.append(
                " %d files uploaded (%d reused),"
                " %d files skipped,"
                " %d directories created (%d reused),"
                " %d directories skipped" % (
                    self._files_created,
                    self._files_reused,
                    self._files_skipped,
                    self._directories_created,
                    self._directories_reused,
                    self._directories_skipped,
                ),
            )

        if verbosity >= 2:
            result.append(
                " %d files checked, %d directories checked" % (
                    files_checked,
                    directories_checked,
                ),
            )
        # calc elapsed time, omitting microseconds
        elapsed_time = str(
            self._end_timestamp - self._start_timestamp
        ).split('.')[0]
        result.append(" backup done, elapsed time: %s" % (elapsed_time,))

        return "\n".join(result)


class BackupProgress(object):
    # Would be nice if this data structure were immutable and its methods were
    # transformations that created a new slightly different object.  Not there
    # yet, though.
    def __init__(self, warn, start_timestamp, target_count):
        self._warn = warn
        self._start_timestamp = start_timestamp
        self._target_count = target_count
        self._files_created = 0
        self._files_reused = 0
        self._files_skipped = 0
        self._directories_created = 0
        self._directories_reused = 0
        self._directories_skipped = 0
        self.last_dircap = None
        self._create_contents = {}
        self._compare_contents = {}

    def report(self, now):
        report_format = (
            "Backing up {target_progress}/{target_total}... {elapsed} elapsed..."
        )
        return report_format.format(
            target_progress=(
                self._files_created
                + self._files_reused
                + self._files_skipped
                + self._directories_created
                + self._directories_reused
                + self._directories_skipped
            ),
            target_total=self._target_count,
            elapsed=self._format_elapsed(now - self._start_timestamp),
        )

    def _format_elapsed(self, elapsed):
        seconds = int(elapsed.total_seconds())
        hours = seconds // 3600
        minutes = (seconds // 60) % 60
        seconds = seconds % 60
        return "{}h {}m {}s".format(
            hours,
            minutes,
            seconds,
        )

    def backup_finished(self):
        end_timestamp = datetime.datetime.now()
        return BackupComplete(
            self._start_timestamp,
            end_timestamp,
            self._files_created,
            self._files_reused,
            self._files_skipped,
            self._directories_created,
            self._directories_reused,
            self._directories_skipped,
            self.last_dircap,
        )

    def consume_directory(self, dirpath):
        return self, {
            os.path.basename(create_path): create_value
            for (create_path, create_value)
            in list(self._create_contents.items())
            if os.path.dirname(create_path) == dirpath
        }, {
            os.path.basename(compare_path): compare_value
            for (compare_path, compare_value)
            in list(self._compare_contents.items())
            if os.path.dirname(compare_path) == dirpath
        }

    def created_directory(self, path, dircap, metadata):
        self._create_contents[path] = ("dirnode", dircap, metadata)
        self._compare_contents[path] = dircap
        self._directories_created += 1
        self.last_dircap = dircap
        return self

    def reused_directory(self, path, dircap, metadata):
        self._create_contents[path] = ("dirnode", dircap, metadata)
        self._compare_contents[path] = dircap
        self._directories_reused += 1
        self.last_dircap = dircap
        return self

    def created_file(self, path, cap, metadata):
        self._create_contents[path] = ("filenode", cap, metadata)
        self._compare_contents[path] = cap
        self._files_created += 1
        return self

    def reused_file(self, path, cap, metadata):
        self._create_contents[path] = ("filenode", cap, metadata)
        self._compare_contents[path] = cap
        self._files_reused += 1
        return self

    def permission_denied(self, isdir, quoted_path):
        return self._skip(
            "WARNING: permission denied on {kind} {path}",
            isdir,
            path=quoted_path,
        )

    def decoding_failed(self, isdir, quoted_path):
        return self._skip(
            "WARNING: could not list {kind} {path} due to a filename encoding error",
            isdir,
            path=quoted_path,
        )

    def unsupported_filetype(self, isdir, quoted_path, filetype):
        return self._skip(
            "WARNING: cannot backup {filetype} {path}",
            isdir,
            path=quoted_path,
            filetype=filetype,
        )

    def _skip(self, message, isdir, **kw):
        if isdir:
            self._directories_skipped += 1
            kind = "directory"
        else:
            self._files_skipped += 1
            kind = "file"
        self._warn(message.format(kind=kind, **kw))
        # Pretend we're a persistent data structure being transformed.
        return self
