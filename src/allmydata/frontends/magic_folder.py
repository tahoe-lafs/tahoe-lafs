
import sys, os
import os.path
from errno import EEXIST
from collections import deque
from datetime import datetime
import time
import ConfigParser

from twisted.internet import defer, reactor, task
from twisted.internet.error import AlreadyCancelled
from twisted.python.failure import Failure
from twisted.python import runtime
from twisted.python import log as twlog
from twisted.application import service

from zope.interface import Interface, Attribute, implementer

from allmydata.util import fileutil, configutil, yamlutil
from allmydata.interfaces import IDirectoryNode
from allmydata.util import log
from allmydata.util.fileutil import (
    precondition_abspath,
    get_pathinfo,
    ConflictError,
    abspath_expanduser_unicode,
)
from allmydata.util.assertutil import precondition, _assert
from allmydata.util.deferredutil import HookMixin
from allmydata.util.progress import PercentProgress
from allmydata.util.encodingutil import listdir_filepath, to_filepath, \
     extend_filepath, unicode_from_filepath, unicode_segments_from, \
     quote_filepath, quote_local_unicode_path, quote_output, FilenameEncodingError
from allmydata.util.time_format import format_time
from allmydata.immutable.upload import FileName, Data
from allmydata import magicfolderdb, magicpath


# Mask off all non-owner permissions for magic-folders files by default.
_DEFAULT_DOWNLOAD_UMASK = 0o077

IN_EXCL_UNLINK = 0x04000000L


class ConfigurationError(Exception):
    """
    There was something wrong with some magic-folder configuration.
    """


def get_inotify_module():
    try:
        if sys.platform == "win32":
            from allmydata.windows import inotify
        elif runtime.platform.supportsINotify():
            from twisted.internet import inotify
        elif sys.platform != "linux":
            from allmydata.watchdog import inotify
        else:
            raise NotImplementedError("filesystem notification needed for Magic Folder is not supported.\n"
                                      "This currently requires Linux or Windows.")
        return inotify
    except (ImportError, AttributeError) as e:
        log.msg(e)
        if sys.platform == "win32":
            raise NotImplementedError("filesystem notification needed for Magic Folder is not supported.\n"
                                      "Windows support requires at least Vista, and has only been tested on Windows 7.")
        raise


def is_new_file(pathinfo, db_entry):
    if db_entry is None:
        return True

    if not pathinfo.exists and db_entry.size is None:
        return False

    return ((pathinfo.size, pathinfo.ctime_ns, pathinfo.mtime_ns) !=
            (db_entry.size, db_entry.ctime_ns, db_entry.mtime_ns))


def _upgrade_magic_folder_config(basedir):
    """
    Helper that upgrades from single-magic-folder-only configs to
    multiple magic-folder configuration style (in YAML)
    """
    config_fname = os.path.join(basedir, "tahoe.cfg")
    config = configutil.get_config(config_fname)

    collective_fname = os.path.join(basedir, "private", "collective_dircap")
    upload_fname = os.path.join(basedir, "private", "magic_folder_dircap")
    magic_folders = {
        u"default": {
            u"directory": config.get("magic_folder", "local.directory").decode("utf-8"),
            u"collective_dircap": fileutil.read(collective_fname),
            u"upload_dircap": fileutil.read(upload_fname),
            u"poll_interval": int(config.get("magic_folder", "poll_interval")),
        },
    }
    fileutil.move_into_place(
        source=os.path.join(basedir, "private", "magicfolderdb.sqlite"),
        dest=os.path.join(basedir, "private", "magicfolder_default.sqlite"),
    )
    save_magic_folders(basedir, magic_folders)
    config.remove_option("magic_folder", "local.directory")
    config.remove_option("magic_folder", "poll_interval")
    configutil.write_config(os.path.join(basedir, 'tahoe.cfg'), config)
    fileutil.remove_if_possible(collective_fname)
    fileutil.remove_if_possible(upload_fname)


def maybe_upgrade_magic_folders(node_directory):
    """
    If the given node directory is not already using the new-style
    magic-folder config it will be upgraded to do so. (This should
    only be done if the user is running a command that needs to modify
    the config)
    """
    yaml_fname = os.path.join(node_directory, u"private", u"magic_folders.yaml")
    if os.path.exists(yaml_fname):
        # we already have new-style magic folders
        return

    config_fname = os.path.join(node_directory, "tahoe.cfg")
    config = configutil.get_config(config_fname)

    # we have no YAML config; if we have config in tahoe.cfg then we
    # can upgrade it to the YAML-based configuration
    if config.has_option("magic_folder", "local.directory"):
        _upgrade_magic_folder_config(node_directory)


def load_magic_folders(node_directory):
    """
    Loads existing magic-folder configuration and returns it as a dict
    mapping name -> dict of config. This will NOT upgrade from
    old-style to new-style config (but WILL read old-style config and
    return in the same way as if it was new-style).

    :param node_directory: path where node data is stored
    :returns: dict mapping magic-folder-name to its config (also a dict)
    """
    yaml_fname = os.path.join(node_directory, u"private", u"magic_folders.yaml")
    folders = dict()

    config_fname = os.path.join(node_directory, "tahoe.cfg")
    config = configutil.get_config(config_fname)

    if not os.path.exists(yaml_fname):
        # there will still be a magic_folder section in a "new"
        # config, but it won't have local.directory nor poll_interval
        # in it.
        if config.has_option("magic_folder", "local.directory"):
            up_fname = os.path.join(node_directory, "private", "magic_folder_dircap")
            coll_fname = os.path.join(node_directory, "private", "collective_dircap")
            directory = config.get("magic_folder", "local.directory").decode('utf8')
            try:
                interval = int(config.get("magic_folder", "poll_interval"))
            except ConfigParser.NoOptionError:
                interval = 60

            if config.has_option("magic_folder", "download.umask"):
                umask = int(config.get("magic_folder", "download.umask"), 8)
            else:
                umask = _DEFAULT_DOWNLOAD_UMASK

            folders[u"default"] = {
                u"directory": directory,
                u"upload_dircap": fileutil.read(up_fname),
                u"collective_dircap": fileutil.read(coll_fname),
                u"poll_interval": interval,
                u"umask": umask,
            }
        else:
            # without any YAML file AND no local.directory option it's
            # an error if magic-folder is "enabled" because we don't
            # actually have enough config for any magic-folders at all
            if config.has_section("magic_folder") \
               and config.getboolean("magic_folder", "enabled") \
               and not folders:
                raise Exception(
                    "[magic_folder] is enabled but has no YAML file and no "
                    "'local.directory' option."
                )

    elif os.path.exists(yaml_fname):  # yaml config-file exists
        if config.has_option("magic_folder", "local.directory"):
            raise Exception(
                "magic-folder config has both old-style configuration"
                " and new-style configuration; please remove the "
                "'local.directory' key from tahoe.cfg or remove "
                "'magic_folders.yaml' from {}".format(node_directory)
            )
        with open(yaml_fname, "r") as f:
            magic_folders = yamlutil.safe_load(f.read())
            if not isinstance(magic_folders, dict):
                raise Exception(
                    "'{}' should contain a dict".format(yaml_fname)
                )

            folders = magic_folders['magic-folders']
            if not isinstance(folders, dict):
                raise Exception(
                    "'magic-folders' in '{}' should be a dict".format(yaml_fname)
                )

    # check configuration
    folders = dict(
        (name, fix_magic_folder_config(yaml_fname, name, config))
        for (name, config)
        in folders.items()
    )
    return folders


def fix_magic_folder_config(yaml_fname, name, config):
    """
    Check the given folder configuration for validity.

    If it refers to a local directory which does not exist, create that
    directory with the configured permissions.

    :param unicode yaml_fname: The configuration file from which the
        configuration was read.

    :param unicode name: The name of the magic-folder this particular
        configuration blob is associated with.

    :param config: The configuration for a single magic-folder.  This is
        expected to be a ``dict`` with certain keys and values of certain
        types but these properties will be checked.

    :raise ConfigurationError: If the given configuration object does not
        conform to some magic-folder configuration requirement.
    """
    if not isinstance(config, dict):
        raise ConfigurationError(
            "Each item in '{}' must itself be a dict".format(yaml_fname)
        )

    for k in ['collective_dircap', 'upload_dircap', 'directory', 'poll_interval']:
        if k not in config:
            raise ConfigurationError(
                "Config for magic folder '{}' is missing '{}'".format(
                    name, k
                )
            )

    if not isinstance(
        config.setdefault(u"umask", _DEFAULT_DOWNLOAD_UMASK),
        int,
    ):
        raise Exception("magic-folder download umask must be an integer")

    # make sure directory for magic folder exists
    dir_fp = to_filepath(config['directory'])
    umask = config.setdefault('umask', 0077)

    try:
        os.mkdir(dir_fp.path, 0777 & (~ umask))
    except OSError as e:
        if EEXIST != e.errno:
            # Report some unknown problem.
            raise ConfigurationError(
                "magic-folder {} configured path {} could not be created: "
                "{}".format(
                    name,
                    dir_fp.path,
                    str(e),
                ),
            )
        elif not dir_fp.isdir():
            # Tell the user there's a collision.
            raise ConfigurationError(
                "magic-folder {} configured path {} exists and is not a "
                "directory".format(
                    name, dir_fp.path,
                ),
            )

    result_config = config.copy()
    for k in ['collective_dircap', 'upload_dircap']:
        if isinstance(config[k], unicode):
            result_config[k] = config[k].encode('ascii')
    return result_config



def save_magic_folders(node_directory, folders):
    fileutil.write_atomically(
        os.path.join(node_directory, u"private", u"magic_folders.yaml"),
        yamlutil.safe_dump({u"magic-folders": folders}),
    )

    config = configutil.get_config(os.path.join(node_directory, u"tahoe.cfg"))
    configutil.set_config(config, "magic_folder", "enabled", "True")
    configutil.write_config(os.path.join(node_directory, u"tahoe.cfg"), config)


class MagicFolder(service.MultiService):

    @classmethod
    def from_config(cls, client_node, name, config):
        """
        Create a ``MagicFolder`` from a client node and magic-folder
        configuration.

        :param _Client client_node: The client node the magic-folder is
            attached to.

        :param dict config: Magic-folder configuration like that in the list
            returned by ``load_magic_folders``.
        """
        db_filename = os.path.join(
            client_node.basedir,
            "private",
            "magicfolder_{}.sqlite".format(name),
        )
        local_dir_config = config['directory']
        try:
            poll_interval = int(config["poll_interval"])
        except ValueError:
            raise ValueError("'poll_interval' option must be an int")

        return cls(
            client=client_node,
            upload_dircap=config["upload_dircap"],
            collective_dircap=config["collective_dircap"],
            local_path_u=abspath_expanduser_unicode(
                local_dir_config,
                base=client_node.basedir,
            ),
            dbfile=abspath_expanduser_unicode(db_filename),
            umask=config["umask"],
            name=name,
            downloader_delay=poll_interval,
        )

    def __init__(self, client, upload_dircap, collective_dircap, local_path_u, dbfile, umask,
                 name, uploader_delay=1.0, clock=None, downloader_delay=60):
        precondition_abspath(local_path_u)
        if not os.path.exists(local_path_u):
            raise ValueError("'{}' does not exist".format(local_path_u))
        if not os.path.isdir(local_path_u):
            raise ValueError("'{}' is not a directory".format(local_path_u))
        # this is used by 'service' things and must be unique in this Service hierarchy
        self.name = 'magic-folder-{}'.format(name)

        service.MultiService.__init__(self)

        clock = clock or reactor
        db = magicfolderdb.get_magicfolderdb(dbfile, create_version=(magicfolderdb.SCHEMA_v1, 1))
        if db is None:
            raise Exception('ERROR: Unable to load magic folder db.')

        # for tests
        self._client = client
        self._db = db

        upload_dirnode = self._client.create_node_from_uri(upload_dircap)
        collective_dirnode = self._client.create_node_from_uri(collective_dircap)

        self.uploader = Uploader(client, local_path_u, db, upload_dirnode, uploader_delay, clock)
        self.downloader = Downloader(client, local_path_u, db, collective_dirnode,
                                     upload_dirnode.get_readonly_uri(), clock, self.uploader.is_pending, umask,
                                     self.set_public_status, poll_interval=downloader_delay)
        self._public_status = (False, ['Magic folder has not yet started'])

    def enable_debug_log(self, enabled=True):
        twlog.msg("enable debug log: %s" % enabled)
        self.uploader.enable_debug_log(enabled)
        self.downloader.enable_debug_log(enabled)

    def get_public_status(self):
        """
        For the web UI, basically.
        """
        return self._public_status

    def set_public_status(self, status, *messages):
        self._public_status = (status, messages)

    def startService(self):
        # TODO: why is this being called more than once?
        if self.running:
            return defer.succeed(None)
        service.MultiService.startService(self)
        return self.uploader.start_monitoring()

    def stopService(self):
        return defer.gatherResults([service.MultiService.stopService(self), self.finish()])

    def ready(self):
        """ready is used to signal us to start
        processing the upload and download items...
        """
        self.uploader.start_uploading()  # synchronous, returns None
        return self.downloader.start_downloading()

    @defer.inlineCallbacks
    def finish(self):
        # must stop these concurrently so that the clock.advance()s
        # work correctly in the tests. Also, it's arguably
        # most-correct.
        d0 = self.downloader.stop()
        d1 = self.uploader.stop()
        yield defer.DeferredList([d0, d1])

    def remove_service(self):
        return service.MultiService.disownServiceParent(self)


class QueueMixin(HookMixin):
    """
    A parent class for Uploader and Downloader that handles putting
    IQueuedItem instances into a work queue and processing
    them. Tracks some history of recent items processed (for the
    "status" API).

    Subclasses implement _scan_delay, _perform_scan and _process

    :ivar _deque: IQueuedItem instances to process

    :ivar _process_history: the last 20 items we processed

    :ivar _in_progress: current batch of items which are currently
        being processed; chunks of work are removed from _deque and
        worked on. As each finishes, it is added to _process_history
        (with oldest items falling off the end).
    """

    def __init__(self, client, local_path_u, db, name, clock):
        self._client = client
        self._local_path_u = local_path_u
        self._local_filepath = to_filepath(local_path_u)
        self._db = db
        self._name = name
        self._clock = clock
        self._debug_log = False
        self._logger = None
        self._hooks = {
            'processed': None,
            'started': None,
            'iteration': None,
            'inotify': None,
            'item_processed': None,
        }
        self.started_d = self.set_hook('started')

        # we should have gotten nice errors already while loading the
        # config, but just to be safe:
        assert self._local_filepath.exists()
        assert self._local_filepath.isdir()

        self._deque = deque()
        # do we also want to bound on "maximum age"?
        self._process_history = deque(maxlen=20)
        self._in_progress = []
        self._stopped = False

        # a Deferred to wait for the _do_processing() loop to exit
        # (gets set to the return from _do_processing() if we get that
        # far)
        self._processing = defer.succeed(None)

    def enable_debug_log(self, enabled=True):
        twlog.msg("queue mixin enable debug logging: %s" % enabled)
        self._debug_log = enabled

    def get_status(self):
        """
        Returns an iterable of instances that implement IQueuedItem
        """
        for item in self._deque:
            yield item
        for item in self._in_progress:
            yield item
        for item in self._process_history:
            yield item

    def _get_filepath(self, relpath_u):
        self._log("_get_filepath(%r)" % (relpath_u,))
        return extend_filepath(self._local_filepath, relpath_u.split(u"/"))

    def _begin_processing(self, res):
        self._log("starting processing loop")
        self._processing = self._do_processing()

        # if there are any errors coming out of _do_processing then
        # our loop is done and we're hosed (i.e. _do_processing()
        # itself has a bug in it)
        def fatal_error(f):
            self._log("internal error: %s" % (f.value,))
            self._log(f)
        self._processing.addErrback(fatal_error)
        return res

    @defer.inlineCallbacks
    def _do_processing(self):
        """
        This is an infinite loop that processes things out of the _deque.

        One iteration runs self._process_deque which calls
        _perform_scan() and then completely drains the _deque
        (processing each item). After that we yield for _turn_deque
        seconds.
        """
        while not self._stopped:
            d = task.deferLater(self._clock, self._scan_delay(), lambda: None)

            # adds items to our deque
            yield self._perform_scan()

            # process anything in our queue
            yield self._process_deque()

            # we want to have our callLater queued in the reactor
            # *before* we trigger the 'iteration' hook, so that hook
            # can successfully advance the Clock and bypass the delay
            # if required (e.g. in the tests).
            self._call_hook(None, 'iteration')
            if not self._stopped:
                yield d

        self._log("stopped")

    def _scan_delay(self):
        raise NotImplementedError

    def _perform_scan(self):
        return

    @defer.inlineCallbacks
    def _process_deque(self):
        # process everything currently in the queue. we're turning it
        # into a list so that if any new items get added while we're
        # processing, they'll not run until next time)
        to_process = list(self._deque)
        self._deque.clear()
        self._count('objects_queued', -len(to_process))

        # we want to include all these in the next status request, so
        # we must put them 'somewhere' before the next yield (and it's
        # not in _process_history because that gets trimmed and we
        # don't want anything to disappear until after it is
        # completed)
        self._in_progress.extend(to_process)

        if to_process:
            self._log("%d items to process" % len(to_process), )
        for item in to_process:
            self._process_history.appendleft(item)
            self._in_progress.remove(item)
            try:
                self._log("  processing '%r'" % (item,))
                proc = yield self._process(item)
                self._log("  done: %r" % proc)
                if not proc:
                    self._process_history.remove(item)
                self._call_hook(item, 'item_processed')
            except Exception as e:
                log.err("processing '%r' failed: %s" % (item, e))
                item.set_status('failed', self._clock.seconds())
                proc = Failure()

            self._call_hook(proc, 'processed')

    def _get_relpath(self, filepath):
        self._log("_get_relpath(%r)" % (filepath,))
        segments = unicode_segments_from(filepath, self._local_filepath)
        self._log("segments = %r" % (segments,))
        return u"/".join(segments)

    def _count(self, counter_name, delta=1):
        ctr = 'magic_folder.%s.%s' % (self._name, counter_name)
        self._client.stats_provider.count(ctr, delta)
        self._log("%s += %r (now %r)" % (counter_name, delta, self._client.stats_provider.counters[ctr]))

    def _logcb(self, res, msg):
        self._log("%s: %r" % (msg, res))
        return res

    def _log(self, msg):
        s = "Magic Folder %s %s: %s" % (quote_output(self._client.nickname), self._name, msg)
        self._client.log(s)
        if self._debug_log:
            twlog.msg(s)

# this isn't in interfaces.py because it's very specific to QueueMixin
class IQueuedItem(Interface):
    relpath_u = Attribute("The path this item represents")
    progress = Attribute("A PercentProgress instance")

    def set_status(self, status, current_time=None):
        """
        """

    def status_time(self, state):
        """
        Get the time of particular state change, or None
        """

    def status_history(self):
        """
        All status changes, sorted latest -> oldest
        """


@implementer(IQueuedItem)
class QueuedItem(object):
    def __init__(self, relpath_u, progress, size):
        self.relpath_u = relpath_u
        self.progress = progress
        self._status_history = dict()
        self.size = size

    def set_status(self, status, current_time=None):
        if current_time is None:
            current_time = time.time()
        self._status_history[status] = current_time

    def status_time(self, state):
        """
        Returns None if there's no status-update for 'state', else returns
        the timestamp when that state was reached.
        """
        return self._status_history.get(state, None)

    def status_history(self):
        """
        Returns a list of 2-tuples of (state, timestamp) sorted by timestamp
        """
        hist = self._status_history.items()
        hist.sort(lambda a, b: cmp(a[1], b[1]))
        return hist

    def __eq__(self, other):
        return (
            other.relpath_u == self.relpath_u,
            other.status_history() == self.status_history(),
        )


class UploadItem(QueuedItem):
    """
    Represents a single item the _deque of the Uploader
    """
    pass


class Uploader(QueueMixin):

    def __init__(self, client, local_path_u, db, upload_dirnode, pending_delay, clock):
        QueueMixin.__init__(self, client, local_path_u, db, 'uploader', clock)

        self.is_ready = False

        if not IDirectoryNode.providedBy(upload_dirnode):
            raise AssertionError("'upload_dircap' does not refer to a directory")
        if upload_dirnode.is_unknown() or upload_dirnode.is_readonly():
            raise AssertionError("'upload_dircap' is not a writecap to a directory")

        self._upload_dirnode = upload_dirnode
        self._inotify = get_inotify_module()
        self._notifier = self._inotify.INotify()

        self._pending = set()  # of unicode relpaths
        self._pending_delay = pending_delay
        self._periodic_full_scan_duration = 10 * 60 # perform a full scan every 10 minutes
        self._periodic_callid = None

        if hasattr(self._notifier, 'set_pending_delay'):
            self._notifier.set_pending_delay(pending_delay)

        # TODO: what about IN_MOVE_SELF and IN_UNMOUNT?
        #
        self.mask = ( self._inotify.IN_CREATE
                    | self._inotify.IN_CLOSE_WRITE
                    | self._inotify.IN_MOVED_TO
                    | self._inotify.IN_MOVED_FROM
                    | self._inotify.IN_DELETE
                    | self._inotify.IN_ONLYDIR
                    | IN_EXCL_UNLINK
                    )
        self._notifier.watch(self._local_filepath, mask=self.mask, callbacks=[self._notify],
                             recursive=True)

    def start_monitoring(self):
        self._log("start_monitoring")
        d = defer.succeed(None)
        d.addCallback(lambda ign: self._notifier.startReading())
        d.addCallback(lambda ign: self._count('dirs_monitored'))
        d.addBoth(self._call_hook, 'started')
        return d

    def stop(self):
        self._log("stop")
        self._stopped = True
        self._notifier.stopReading()
        self._count('dirs_monitored', -1)
        if self._periodic_callid:
            try:
                self._periodic_callid.cancel()
            except AlreadyCancelled:
                pass

        if hasattr(self._notifier, 'wait_until_stopped'):
            d = self._notifier.wait_until_stopped()
        else:
            d = defer.succeed(None)
        # wait for processing loop to actually exit
        d.addCallback(lambda ign: self._processing)
        return d

    def start_uploading(self):
        self._log("start_uploading")
        self.is_ready = True

        all_relpaths = self._db.get_all_relpaths()
        self._log("all relpaths: %r" % (all_relpaths,))

        for relpath_u in all_relpaths:
            self._add_pending(relpath_u)

        self._full_scan()
        # XXX changed this while re-basing; double check we can
        # *really* just call this synchronously.
        return self._begin_processing(None)

    def _scan_delay(self):
        return self._pending_delay

    def _full_scan(self):
        self._periodic_callid = self._clock.callLater(self._periodic_full_scan_duration, self._full_scan)
        self._log("FULL SCAN")
        self._log("_pending %r" % (self._pending))
        self._scan(u"")

    def _add_pending(self, relpath_u):
        self._log("add pending %r" % (relpath_u,))
        if magicpath.should_ignore_file(relpath_u):
            self._log("_add_pending %r but should_ignore()==True" % (relpath_u,))
            return
        if relpath_u in self._pending:
            self._log("_add_pending %r but already pending" % (relpath_u,))
            return

        self._pending.add(relpath_u)
        fp = self._get_filepath(relpath_u)
        pathinfo = get_pathinfo(unicode_from_filepath(fp))
        progress = PercentProgress()
        self._log(u"add pending size: {}: {}".format(relpath_u, pathinfo.size))
        item = UploadItem(relpath_u, progress, pathinfo.size)
        item.set_status('queued', self._clock.seconds())
        self._deque.append(item)
        self._count('objects_queued')
        self._log("_add_pending(%r) queued item" % (relpath_u,))

    def _scan(self, reldir_u):
        # Scan a directory by (synchronously) adding the paths of all its children to self._pending.
        # Note that this doesn't add them to the deque -- that will

        self._log("SCAN '%r'" % (reldir_u,))
        fp = self._get_filepath(reldir_u)
        try:
            children = listdir_filepath(fp)
        except EnvironmentError:
            raise Exception("WARNING: magic folder: permission denied on directory %s"
                            % quote_filepath(fp))
        except FilenameEncodingError:
            raise Exception("WARNING: magic folder: could not list directory %s due to a filename encoding error"
                            % quote_filepath(fp))

        for child in children:
            self._log("   scan; child %r" % (child,))
            _assert(isinstance(child, unicode), child=child)
            self._add_pending("%s/%s" % (reldir_u, child) if reldir_u != u"" else child)

    def is_pending(self, relpath_u):
        return relpath_u in self._pending

    def _notify(self, opaque, path, events_mask):
        # Twisted doesn't seem to do anything if our callback throws
        # an error, so...
        try:
            return self._real_notify(opaque, path, events_mask)
        except Exception as e:
            self._log(u"error calling _real_notify: {}".format(e))
            twlog.err(Failure(), "Error calling _real_notify")

    def _real_notify(self, opaque, path, events_mask):
        self._log("inotify event %r, %r, %r\n" % (opaque, path, ', '.join(self._inotify.humanReadableMask(events_mask))))
        relpath_u = self._get_relpath(path)

        # We filter out IN_CREATE events not associated with a directory.
        # Acting on IN_CREATE for files could cause us to read and upload
        # a possibly-incomplete file before the application has closed it.
        # There should always be an IN_CLOSE_WRITE after an IN_CREATE, I think.
        # It isn't possible to avoid watching for IN_CREATE at all, because
        # it is the only event notified for a directory creation.

        if ((events_mask & self._inotify.IN_CREATE) != 0 and
            (events_mask & self._inotify.IN_ISDIR) == 0):
            self._log("ignoring event for %r (creation of non-directory)\n" % (relpath_u,))
            return
        if relpath_u in self._pending:
            self._log("not queueing %r because it is already pending" % (relpath_u,))
            return
        if magicpath.should_ignore_file(relpath_u):
            self._log("ignoring event for %r (ignorable path)" % (relpath_u,))
            return

        self._add_pending(relpath_u)
        self._call_hook(path, 'inotify')

    def _process(self, item):
        """
        process a single QueuedItem. If this returns False, the item is
        removed from _process_history
        """
        # Uploader
        relpath_u = item.relpath_u
        self._log("_process(%r)" % (relpath_u,))
        item.set_status('started', self._clock.seconds())

        if relpath_u is None:
            item.set_status('invalid_path', self._clock.seconds())
            return defer.succeed(False)
        precondition(isinstance(relpath_u, unicode), relpath_u)
        precondition(not relpath_u.endswith(u'/'), relpath_u)

        d = defer.succeed(False)

        def _maybe_upload(ign, now=None):
            self._log("_maybe_upload: relpath_u=%r, now=%r" % (relpath_u, now))
            if now is None:
                now = time.time()
            fp = self._get_filepath(relpath_u)
            pathinfo = get_pathinfo(unicode_from_filepath(fp))

            self._log("about to remove %r from pending set %r" %
                      (relpath_u, self._pending))
            try:
                self._pending.remove(relpath_u)
            except KeyError:
                self._log("WRONG that %r wasn't in pending" % (relpath_u,))
            encoded_path_u = magicpath.path2magic(relpath_u)

            if not pathinfo.exists:
                # FIXME merge this with the 'isfile' case.
                self._log("notified object %s disappeared (this is normal)" % quote_filepath(fp))
                self._count('objects_disappeared')

                db_entry = self._db.get_db_entry(relpath_u)
                if db_entry is None:
                    return False

                last_downloaded_timestamp = now  # is this correct?

                if is_new_file(pathinfo, db_entry):
                    new_version = db_entry.version + 1
                else:
                    self._log("Not uploading %r" % (relpath_u,))
                    self._count('objects_not_uploaded')
                    return False

                # look out! there's another place we set a "metadata"
                # object like this (for new, not deleted files)
                metadata = {
                    'version': new_version,
                    'deleted': True,
                    'last_downloaded_timestamp': last_downloaded_timestamp,
                    'user_mtime': pathinfo.ctime_ns / 1000000000.0,  # why are we using ns in PathInfo??
                }

                # from the Fire Dragons part of the spec:
                # Later, in response to a local filesystem change at a given path, the
                # Magic Folder client reads the last-downloaded record associated with
                # that path (if any) from the database and then uploads the current
                # file. When it links the uploaded file into its client DMD, it
                # includes the ``last_downloaded_uri`` field in the metadata of the
                # directory entry, overwriting any existing field of that name. If
                # there was no last-downloaded record associated with the path, this
                # field is omitted.
                # Note that ``last_downloaded_uri`` field does *not* record the URI of
                # the uploaded file (which would be redundant); it records the URI of
                # the last download before the local change that caused the upload.
                # The field will be absent if the file has never been downloaded by
                # this client (i.e. if it was created on this client and no change
                # by any other client has been detected).

                # XXX currently not actually true: it will record the
                # LAST THING we wrote to (or saw on) disk (not
                # necessarily downloaded?)

                if db_entry.last_downloaded_uri is not None:
                    metadata['last_downloaded_uri'] = db_entry.last_downloaded_uri
                if db_entry.last_uploaded_uri is not None:
                    metadata['last_uploaded_uri'] = db_entry.last_uploaded_uri

                empty_uploadable = Data("", self._client.convergence)
                d2 = self._upload_dirnode.add_file(
                    encoded_path_u, empty_uploadable,
                    metadata=metadata,
                    overwrite=True,
                    progress=item.progress,
                )

                def _add_db_entry(filenode):
                    filecap = filenode.get_uri()
                    # if we're uploading a file, we want to set
                    # last_downloaded_uri to the filecap so that we don't
                    # immediately re-download it when we start up next
                    last_downloaded_uri = metadata.get('last_downloaded_uri', filecap)
                    self._db.did_upload_version(
                        relpath_u,
                        new_version,
                        filecap,
                        last_downloaded_uri,
                        last_downloaded_timestamp,
                        pathinfo,
                    )
                    self._count('files_uploaded')
                d2.addCallback(_add_db_entry)
                d2.addCallback(lambda ign: True)
                return d2
            elif pathinfo.islink:
                self.warn("WARNING: cannot upload symlink %s" % quote_filepath(fp))
                return False
            elif pathinfo.isdir:
                self._log("ISDIR")
                if not getattr(self._notifier, 'recursive_includes_new_subdirectories', False):
                    self._notifier.watch(fp, mask=self.mask, callbacks=[self._notify], recursive=True)

                db_entry = self._db.get_db_entry(relpath_u)
                self._log("isdir dbentry %r" % (db_entry,))
                if not is_new_file(pathinfo, db_entry):
                    self._log("NOT A NEW FILE")
                    return False

                uploadable = Data("", self._client.convergence)
                encoded_path_u += magicpath.path2magic(u"/")
                self._log("encoded_path_u =  %r" % (encoded_path_u,))
                upload_d = self._upload_dirnode.add_file(
                    encoded_path_u, uploadable,
                    metadata={"version": 0},
                    overwrite=True,
                    progress=item.progress,
                )
                def _dir_succeeded(ign):
                    self._log("created subdirectory %r" % (relpath_u,))
                    self._count('directories_created')
                def _dir_failed(f):
                    self._log("failed to create subdirectory %r" % (relpath_u,))
                    return f
                upload_d.addCallbacks(_dir_succeeded, _dir_failed)
                upload_d.addCallback(lambda ign: self._scan(relpath_u))
                upload_d.addCallback(lambda ign: True)
                return upload_d
            elif pathinfo.isfile:
                db_entry = self._db.get_db_entry(relpath_u)

                last_downloaded_timestamp = now

                if db_entry is None:
                    new_version = 0
                elif is_new_file(pathinfo, db_entry):
                    new_version = db_entry.version + 1
                else:
                    self._log("Not uploading %r" % (relpath_u,))
                    self._count('objects_not_uploaded')
                    return False

                metadata = {
                    'version': new_version,
                    'last_downloaded_timestamp': last_downloaded_timestamp,
                    'user_mtime': pathinfo.mtime_ns / 1000000000.0,  # why are we using ns in PathInfo??
                }
                if db_entry is not None:
                    if db_entry.last_downloaded_uri is not None:
                        metadata['last_downloaded_uri'] = db_entry.last_downloaded_uri
                    if db_entry.last_uploaded_uri is not None:
                        metadata['last_uploaded_uri'] = db_entry.last_uploaded_uri

                uploadable = FileName(unicode_from_filepath(fp), self._client.convergence)
                d2 = self._upload_dirnode.add_file(
                    encoded_path_u, uploadable,
                    metadata=metadata,
                    overwrite=True,
                    progress=item.progress,
                )

                def _add_db_entry(filenode):
                    filecap = filenode.get_uri()
                    # if we're uploading a file, we want to set
                    # last_downloaded_uri to the filecap so that we don't
                    # immediately re-download it when we start up next
                    last_downloaded_uri = filecap
                    self._db.did_upload_version(
                        relpath_u,
                        new_version,
                        filecap,
                        last_downloaded_uri,
                        last_downloaded_timestamp,
                        pathinfo
                    )
                    self._count('files_uploaded')
                    return True
                d2.addCallback(_add_db_entry)
                return d2
            else:
                self.warn("WARNING: cannot process special file %s" % quote_filepath(fp))
                return False

        d.addCallback(_maybe_upload)

        def _succeeded(res):
            self._log("_succeeded(%r)" % (res,))
            if res:
                self._count('objects_succeeded')
            # TODO: maybe we want the status to be 'ignored' if res is False
            item.set_status('success', self._clock.seconds())
            return res
        def _failed(f):
            self._count('objects_failed')
            self._log("%s while processing %r" % (f, relpath_u))
            item.set_status('failure', self._clock.seconds())
            return f
        d.addCallbacks(_succeeded, _failed)
        return d

    def _get_metadata(self, encoded_path_u):
        try:
            d = self._upload_dirnode.get_metadata_for(encoded_path_u)
        except KeyError:
            return Failure()
        return d

    def _get_filenode(self, encoded_path_u):
        try:
            d = self._upload_dirnode.get(encoded_path_u)
        except KeyError:
            return Failure()
        return d


class WriteFileMixin(object):
    FUDGE_SECONDS = 10.0

    def _get_conflicted_filename(self, abspath_u):
        return abspath_u + u".conflict"

    def _write_downloaded_file(self, local_path_u, abspath_u, file_contents,
                               is_conflict=False, now=None, mtime=None):
        self._log(
            ("_write_downloaded_file({abspath}, <{file_size} bytes>,"
             " is_conflict={is_conflict}, now={now}, mtime={mtime})").format(
                 abspath=abspath_u,
                 file_size=len(file_contents),
                 is_conflict=is_conflict,
                 now=now,
                 mtime=mtime,
             )
        )

        # 1. Write a temporary file, say .foo.tmp.
        # 2. is_conflict determines whether this is an overwrite or a conflict.
        # 3. Set the mtime of the replacement file to be T seconds before the
        #    current local time, or mtime whichever is oldest
        # 4. Perform a file replacement with backup filename foo.backup,
        #    replaced file foo, and replacement file .foo.tmp. If any step of
        #    this operation fails, reclassify as a conflict and stop.
        #
        # Returns the path of the destination file.

        precondition_abspath(abspath_u)
        replacement_path_u = abspath_u + u".tmp"  # FIXME more unique
        if now is None:
            now = time.time()

        initial_path_u = os.path.dirname(abspath_u)
        fileutil.make_dirs_with_absolute_mode(local_path_u, initial_path_u, (~ self._umask) & 0777)
        fileutil.write(replacement_path_u, file_contents)
        os.chmod(replacement_path_u, (~ self._umask) & 0666)

        # FUDGE_SECONDS is used to determine if another process has
        # written to the same file concurrently. This is described in
        # the Earth Dragon section of our design document ("T" in the
        # spec is FUDGE_SECONDS here):
        # docs/proposed/magic-folder/remote-to-local-sync.rst
        fudge_time = now - self.FUDGE_SECONDS
        modified_time = min(fudge_time, mtime) if mtime else fudge_time
        os.utime(replacement_path_u, (now, modified_time))
        if is_conflict:
            return self._rename_conflicted_file(abspath_u, replacement_path_u)
        else:
            try:
                fileutil.replace_file(abspath_u, replacement_path_u)
                return abspath_u
            except fileutil.ConflictError as e:
                self._log("overwrite becomes _conflict: {}".format(e))
                return self._rename_conflicted_file(abspath_u, replacement_path_u)

    def _rename_conflicted_file(self, abspath_u, replacement_path_u):
        self._log("_rename_conflicted_file(%r, %r)" % (abspath_u, replacement_path_u))

        conflict_path_u = self._get_conflicted_filename(abspath_u)
        #if os.path.isfile(replacement_path_u):
        #    print "%r exists" % (replacement_path_u,)
        #if os.path.isfile(conflict_path_u):
        #    print "%r exists" % (conflict_path_u,)

        fileutil.rename_no_overwrite(replacement_path_u, conflict_path_u)
        return conflict_path_u

    def _rename_deleted_file(self, abspath_u):
        self._log('renaming deleted file to backup: %s' % (abspath_u,))
        try:
            fileutil.rename_no_overwrite(abspath_u, abspath_u + u'.backup')
        except OSError:
            self._log("Already gone: '%s'" % (abspath_u,))
        return abspath_u


def _is_empty_filecap(client, cap):
    """
    Internal helper.

    :param cap: a capability URI

    :returns: True if "cap" represents an empty file
    """
    node = client.create_node_from_uri(
        None,
        cap.encode('ascii'),
    )
    return (not node.get_size())


class DownloadItem(QueuedItem):
    """
    Represents a single item in the _deque of the Downloader
    """
    def __init__(self, relpath_u, progress, filenode, metadata, size):
        super(DownloadItem, self).__init__(relpath_u, progress, size)
        self.file_node = filenode
        self.metadata = metadata


class Downloader(QueueMixin, WriteFileMixin):

    def __init__(self, client, local_path_u, db, collective_dirnode,
                 upload_readonly_dircap, clock, is_upload_pending, umask,
                 status_reporter, poll_interval=60):
        QueueMixin.__init__(self, client, local_path_u, db, 'downloader', clock)

        if not IDirectoryNode.providedBy(collective_dirnode):
            raise AssertionError("'collective_dircap' does not refer to a directory")
        if collective_dirnode.is_unknown() or not collective_dirnode.is_readonly():
            raise AssertionError("'collective_dircap' is not a readonly cap to a directory")

        self._collective_dirnode = collective_dirnode
        self._upload_readonly_dircap = upload_readonly_dircap
        self._is_upload_pending = is_upload_pending
        self._umask = umask
        self._status_reporter = status_reporter
        self._poll_interval = poll_interval

    @defer.inlineCallbacks
    def start_downloading(self):
        self._log("start_downloading")
        files = self._db.get_all_relpaths()
        self._log("all files %s" % files)

        while True:
            try:
                data = yield self._scan_remote_collective(scan_self=True)
                twlog.msg("Completed initial Magic Folder scan successfully ({})".format(self))
                x = yield self._begin_processing(data)
                defer.returnValue(x)
                break

            except Exception as e:
                self._status_reporter(
                    False, "Initial scan has failed",
                    "Last tried at %s" % self.nice_current_time(),
                )
                twlog.msg("Magic Folder failed initial scan: %s" % (e,))
                yield task.deferLater(self._clock, self._poll_interval, lambda: None)

    def nice_current_time(self):
        return format_time(datetime.fromtimestamp(self._clock.seconds()).timetuple())

    def stop(self):
        self._log("stop")
        self._stopped = True
        d = defer.succeed(None)
        # wait for processing loop to actually exit
        d.addCallback(lambda ign: self._processing)
        return d

    def _should_download(self, relpath_u, remote_version, remote_uri):
        """
        _should_download returns a bool indicating whether or not a remote object should be downloaded.
        We check the remote metadata version against our magic-folder db version number;
        latest version wins.
        """
        if magicpath.should_ignore_file(relpath_u):
            return False
        db_entry = self._db.get_db_entry(relpath_u)
        if db_entry is None:
            return True
        if db_entry.version < remote_version:
            return True
        if db_entry.last_downloaded_uri is None and _is_empty_filecap(self._client, remote_uri):
            pass
        elif db_entry.last_downloaded_uri != remote_uri:
            return True
        return False

    def _get_local_latest(self, relpath_u):
        """
        _get_local_latest takes a unicode path string checks to see if this file object
        exists in our magic-folder db; if not then return None
        else check for an entry in our magic-folder db and return it.
        """
        if not self._get_filepath(relpath_u).exists():
            return None
        return self._db.get_db_entry(relpath_u)

    def _get_collective_latest_file(self, filename):
        """
        _get_collective_latest_file takes a file path pointing to a file managed by
        magic-folder and returns a deferred that fires with the two tuple containing a
        file node and metadata for the latest version of the file located in the
        magic-folder collective directory.
        """
        collective_dirmap_d = self._collective_dirnode.list()
        def scan_collective(result):
            list_of_deferreds = []
            for dir_name in result.keys():
                # XXX make sure it's a directory
                d = defer.succeed(None)
                d.addCallback(lambda x, dir_name=dir_name: result[dir_name][0].get_child_and_metadata(filename))
                list_of_deferreds.append(d)
            deferList = defer.DeferredList(list_of_deferreds, consumeErrors=True)
            return deferList
        collective_dirmap_d.addCallback(scan_collective)
        def highest_version(deferredList):
            max_version = 0
            metadata = None
            node = None
            for success, result in deferredList:
                if success:
                    if node is None or result[1]['version'] > max_version:
                        node, metadata = result
                        max_version = result[1]['version']
            return node, metadata
        collective_dirmap_d.addCallback(highest_version)
        return collective_dirmap_d

    def _scan_remote_dmd(self, nickname, dirnode, scan_batch):
        self._log("_scan_remote_dmd nickname %r" % (nickname,))
        d = dirnode.list()
        def scan_listing(listing_map):
            for encoded_relpath_u in listing_map.keys():
                relpath_u = magicpath.magic2path(encoded_relpath_u)
                self._log("found %r" % (relpath_u,))

                file_node, metadata = listing_map[encoded_relpath_u]
                local_dbentry = self._get_local_latest(relpath_u)

                # XXX FIXME this is *awefully* similar to
                # _should_download code in function etc -- can we
                # share?
                remote_version = metadata.get('version', None)
                remote_uri = file_node.get_readonly_uri()
                self._log("%r has local dbentry %r, remote version %r, remote uri %r"
                          % (relpath_u, local_dbentry, remote_version, remote_uri))

                if (local_dbentry is None or remote_version is None or
                    local_dbentry.version < remote_version or
                    (local_dbentry.version == remote_version and local_dbentry.last_downloaded_uri != remote_uri)):
                    self._log("%r added to download queue" % (relpath_u,))
                    if scan_batch.has_key(relpath_u):
                        scan_batch[relpath_u] += [(file_node, metadata)]
                    else:
                        scan_batch[relpath_u] = [(file_node, metadata)]
            self._status_reporter(
                True, 'Magic folder is working',
                'Last scan: %s' % self.nice_current_time(),
            )

        d.addCallback(scan_listing)
        d.addBoth(self._logcb, "end of _scan_remote_dmd")
        return d

    def _scan_remote_collective(self, scan_self=False):
        self._log("_scan_remote_collective")
        scan_batch = {}  # path -> [(filenode, metadata)]

        d = self._collective_dirnode.list()
        def scan_collective(dirmap):
            d2 = defer.succeed(None)
            for dir_name in dirmap:
                (dirnode, metadata) = dirmap[dir_name]
                if scan_self or dirnode.get_readonly_uri() != self._upload_readonly_dircap:
                    d2.addCallback(lambda ign, dir_name=dir_name, dirnode=dirnode:
                                   self._scan_remote_dmd(dir_name, dirnode, scan_batch))
                    def _err(f, dir_name=dir_name):
                        self._log("failed to scan DMD for client %r: %s" % (dir_name, f))
                        # XXX what should we do to make this failure more visible to users?
                    d2.addErrback(_err)

            return d2
        d.addCallback(scan_collective)

        def _filter_batch_to_deque(ign):
            self._log("deque = %r, scan_batch = %r" % (self._deque, scan_batch))
            for relpath_u in scan_batch.keys():
                file_node, metadata = max(scan_batch[relpath_u], key=lambda x: x[1]['version'])

                if self._should_download(relpath_u, metadata['version'], file_node.get_readonly_uri()):
                    to_dl = DownloadItem(
                        relpath_u,
                        PercentProgress(file_node.get_size()),
                        file_node,
                        metadata,
                        file_node.get_size(),
                    )
                    to_dl.set_status('queued', self._clock.seconds())
                    self._deque.append(to_dl)
                    self._count("objects_queued")
                else:
                    self._log("Excluding %r" % (relpath_u,))
                    self._call_hook(None, 'processed', async=True)  # await this maybe-Deferred??

            self._log("deque after = %r" % (self._deque,))
        d.addCallback(_filter_batch_to_deque)
        return d


    def _scan_delay(self):
        return self._poll_interval

    @defer.inlineCallbacks
    def _perform_scan(self):
        x = None
        try:
            x = yield self._scan_remote_collective()
            self._status_reporter(
                True, 'Magic folder is working',
                'Last scan: %s' % self.nice_current_time(),
            )
        except Exception as e:
            twlog.msg("Remote scan failed: %s" % (e,))
            self._log("_scan failed: %s" % (repr(e),))
            self._status_reporter(
                False, 'Remote scan has failed: %s' % str(e),
                'Last attempted at %s' % self.nice_current_time(),
            )
        defer.returnValue(x)

    def _process(self, item):
        # Downloader
        self._log("_process(%r)" % (item,))
        now = self._clock.seconds()

        self._log("started! %s" % (now,))
        item.set_status('started', now)
        fp = self._get_filepath(item.relpath_u)
        abspath_u = unicode_from_filepath(fp)
        conflict_path_u = self._get_conflicted_filename(abspath_u)
        last_uploaded_uri = item.metadata.get('last_uploaded_uri', None)

        d = defer.succeed(False)

        def do_update_db(written_abspath_u):
            filecap = item.file_node.get_uri()
            if not item.file_node.get_size():
                filecap = None  # ^ is an empty file
            last_downloaded_uri = filecap
            last_downloaded_timestamp = now
            written_pathinfo = get_pathinfo(written_abspath_u)

            if not written_pathinfo.exists and not item.metadata.get('deleted', False):
                raise Exception("downloaded object %s disappeared" % quote_local_unicode_path(written_abspath_u))

            self._db.did_upload_version(
                item.relpath_u,
                item.metadata['version'],
                last_uploaded_uri,
                last_downloaded_uri,
                last_downloaded_timestamp,
                written_pathinfo,
            )
            self._count('objects_downloaded')
            item.set_status('success', self._clock.seconds())
            return True

        def failed(f):
            item.set_status('failure', self._clock.seconds())
            self._log("download failed: %s" % (str(f),))
            self._count('objects_failed')
            return f

        if os.path.isfile(conflict_path_u):
            def fail(res):
                raise ConflictError("download failed: already conflicted: %r" % (item.relpath_u,))
            d.addCallback(fail)
        else:

            # Let ``last_downloaded_uri`` be the field of that name obtained from
            # the directory entry metadata for ``foo`` in Bob's DMD (this field
            # may be absent). Then the algorithm is:

            # * 2a. Attempt to "stat" ``foo`` to get its *current statinfo* (size
            #   in bytes, ``mtime``, and ``ctime``). If Alice has no local copy
            #   of ``foo``, classify as an overwrite.

            current_statinfo = get_pathinfo(abspath_u)

            is_conflict = False
            db_entry = self._db.get_db_entry(item.relpath_u)
            dmd_last_downloaded_uri = item.metadata.get('last_downloaded_uri', None)

            # * 2b. Read the following information for the path ``foo`` from the
            #   local magic folder db:
            #   * the *last-seen statinfo*, if any (this is the size in
            #     bytes, ``mtime``, and ``ctime`` stored in the ``local_files``
            #     table when the file was last uploaded);
            #   * the ``last_uploaded_uri`` field of the ``local_files`` table
            #     for this file, which is the URI under which the file was last
            #     uploaded.

            if db_entry:
                # * 2c. If any of the following are true, then classify as a conflict:
                #   * i. there are pending notifications of changes to ``foo``;
                #   * ii. the last-seen statinfo is either absent (i.e. there is
                #     no entry in the database for this path), or different from the
                #     current statinfo;

                if current_statinfo.exists:
                    self._log("checking conflicts {}".format(item.relpath_u))
                    if (db_entry.mtime_ns != current_statinfo.mtime_ns or \
                        db_entry.ctime_ns != current_statinfo.ctime_ns or \
                        db_entry.size != current_statinfo.size):
                        is_conflict = True
                        self._log("conflict because local change0")

                    if db_entry.last_downloaded_uri is None \
                       or db_entry.last_uploaded_uri is None \
                       or dmd_last_downloaded_uri is None:
                        # we've never downloaded anything before for this
                        # file, but the other side might have created a new
                        # file "at the same time"
                        if db_entry.version >= item.metadata['version']:
                            self._log("conflict because my version >= remote version")
                            is_conflict = True
                    elif dmd_last_downloaded_uri != db_entry.last_downloaded_uri:
                        is_conflict = True
                        self._log("conflict because dmd_last_downloaded_uri != db_entry.last_downloaded_uri")

            else:  # no local db_entry .. but has the file appeared locally meantime?
                if current_statinfo.exists:
                    is_conflict = True
                    self._log("conflict because local change1")

            if is_conflict:
                self._count('objects_conflicted')

            if item.relpath_u.endswith(u"/"):
                if item.metadata.get('deleted', False):
                    self._log("rmdir(%r) ignored" % (abspath_u,))
                else:
                    self._log("mkdir(%r)" % (abspath_u,))
                    d.addCallback(lambda ign: fileutil.make_dirs(abspath_u))
                    d.addCallback(lambda ign: abspath_u)
            else:
                if item.metadata.get('deleted', False):
                    d.addCallback(lambda ign: self._rename_deleted_file(abspath_u))
                else:
                    d.addCallback(lambda ign: item.file_node.download_best_version(progress=item.progress))
                    d.addCallback(
                        lambda contents: self._write_downloaded_file(
                            self._local_path_u, abspath_u, contents,
                            is_conflict=is_conflict,
                            mtime=item.metadata.get('user_mtime', item.metadata.get('tahoe', {}).get('linkmotime')),
                        )
                    )

        d.addCallbacks(do_update_db)
        d.addErrback(failed)

        def trap_conflicts(f):
            f.trap(ConflictError)
            self._log("IGNORE CONFLICT ERROR %r" % f)
            return False
        d.addErrback(trap_conflicts)
        return d
