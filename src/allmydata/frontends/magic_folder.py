
import sys, os
import os.path
from errno import EEXIST
from collections import deque
from datetime import datetime
import time
import ConfigParser

from twisted.python.filepath import FilePath
from twisted.python.monkey import MonkeyPatcher
from twisted.internet import defer, reactor, task
from twisted.internet.error import AlreadyCancelled
from twisted.python.failure import Failure
from twisted.python import runtime
from twisted.application import service

from zope.interface import Interface, Attribute, implementer

from eliot import (
    Field,
    Message,
    start_action,
    ActionType,
    MessageType,
    write_failure,
    write_traceback,
    log_call,
)
from eliot.twisted import (
    DeferredContext,
)

from allmydata.util import (
    fileutil,
    configutil,
    yamlutil,
    eliotutil,
)
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
     quote_filepath, quote_local_unicode_path, FilenameEncodingError
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


def _get_inotify_module():
    try:
        if sys.platform == "win32":
            from allmydata.windows import inotify
        elif runtime.platform.supportsINotify():
            from twisted.internet import inotify
        elif not sys.platform.startswith("linux"):
            from allmydata.watchdog import inotify
        else:
            raise NotImplementedError("filesystem notification needed for Magic Folder is not supported.\n"
                                      "This currently requires Linux, Windows, or macOS.")
        return inotify
    except (ImportError, AttributeError) as e:
        log.msg(e)
        if sys.platform == "win32":
            raise NotImplementedError("filesystem notification needed for Magic Folder is not supported.\n"
                                      "Windows support requires at least Vista, and has only been tested on Windows 7.")
        raise


def get_inotify_module():
    # Until Twisted #9579 is fixed, the Docker check just screws things up.
    # Disable it.
    monkey = MonkeyPatcher()
    monkey.addPatch(runtime.platform, "isDocker", lambda: False)
    return monkey.runWithPatches(_get_inotify_module)


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
        db_filename = client_node.config.get_private_path("magicfolder_{}.sqlite".format(name))
        local_dir_config = config['directory']
        try:
            poll_interval = int(config["poll_interval"])
        except ValueError:
            raise ValueError("'poll_interval' option must be an int")

        return cls(
            client=client_node,
            upload_dircap=config["upload_dircap"],
            collective_dircap=config["collective_dircap"],
            # XXX surely a better way for this local_path_u business
            local_path_u=abspath_expanduser_unicode(
                local_dir_config,
                base=client_node.config.get_config_path(),
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
        with MAGIC_FOLDER_STOP(nickname=self.name).context():
            d = DeferredContext(self._finish())
        d.addBoth(
            lambda ign: service.MultiService.stopService(self)
        )
        return d.addActionFinish()

    def ready(self):
        """ready is used to signal us to start
        processing the upload and download items...
        """
        self.uploader.start_uploading()  # synchronous, returns None
        return self.downloader.start_downloading()

    def _finish(self):
        d0 = self.downloader.stop()
        d1 = self.uploader.stop()
        return defer.DeferredList(list(
            DeferredContext(d).addErrback(write_failure).result
            for d in [d0, d1]
        ))


_NICKNAME = Field.for_types(
    u"nickname",
    [unicode, bytes],
    u"A Magic-Folder participant nickname.",
)

_DIRECTION = Field.for_types(
    u"direction",
    [unicode],
    u"A synchronization direction: uploader or downloader.",
    eliotutil.validateSetMembership({u"uploader", u"downloader"}),
)

PROCESSING_LOOP = ActionType(
    u"magic-folder:processing-loop",
    [_NICKNAME, _DIRECTION],
    [],
    u"A Magic-Folder is processing uploads or downloads.",
)

ITERATION = ActionType(
    u"magic-folder:iteration",
    [_NICKNAME, _DIRECTION],
    [],
    u"A step towards synchronization in one direction.",
)

_COUNT = Field.for_types(
    u"count",
    [int, long],
    u"The number of items in the processing queue.",
)

PROCESS_QUEUE = ActionType(
    u"magic-folder:process-queue",
    [_COUNT],
    [],
    u"A Magic-Folder is working through an item queue.",
)

SCAN_REMOTE_COLLECTIVE = ActionType(
    u"magic-folder:scan-remote-collective",
    [],
    [],
    u"The remote collective is being scanned for peer DMDs.",
)

SCAN_REMOTE_DMD = ActionType(
    u"magic-folder:scan-remote-dmd",
    [_NICKNAME],
    [],
    u"A peer DMD is being scanned for changes.",
)

REMOTE_VERSION = Field.for_types(
    u"remote_version",
    [int, long],
    u"The version of a path found in a peer DMD.",
)

REMOTE_URI = Field.for_types(
    u"remote_uri",
    [bytes],
    u"The filecap of a path found in a peer DMD.",
)

REMOTE_DMD_ENTRY = MessageType(
    u"magic-folder:remote-dmd-entry",
    [eliotutil.RELPATH, magicfolderdb.PATHENTRY, REMOTE_VERSION, REMOTE_URI],
    u"A single entry found by scanning a peer DMD.",
)

ADD_TO_DOWNLOAD_QUEUE = MessageType(
    u"magic-folder:add-to-download-queue",
    [eliotutil.RELPATH],
    u"An entry was found to be changed and is being queued for download.",
)

MAGIC_FOLDER_STOP = ActionType(
    u"magic-folder:stop",
    [_NICKNAME],
    [],
    u"A Magic-Folder is being stopped.",
)

MAYBE_UPLOAD = MessageType(
    u"magic-folder:maybe-upload",
    [eliotutil.RELPATH],
    u"A decision is being made about whether to upload a file.",
)

PENDING = Field(
    u"pending",
    lambda s: list(s),
    u"The paths which are pending processing.",
    eliotutil.validateInstanceOf(set),
)

REMOVE_FROM_PENDING = ActionType(
    u"magic-folder:remove-from-pending",
    [eliotutil.RELPATH, PENDING],
    [],
    u"An item being processed is being removed from the pending set.",
)

PATH = Field(
    u"path",
    lambda fp: fp.asTextMode().path,
    u"A local filesystem path.",
    eliotutil.validateInstanceOf(FilePath),
)

NOTIFIED_OBJECT_DISAPPEARED = MessageType(
    u"magic-folder:notified-object-disappeared",
    [PATH],
    u"A path which generated a notification was not found on the filesystem.  This is normal.",
)

PROPAGATE_DIRECTORY_DELETION = ActionType(
    u"magic-folder:propagate-directory-deletion",
    [],
    [],
    u"Children of a deleted directory are being queued for upload processing.",
)

NO_DATABASE_ENTRY = MessageType(
    u"magic-folder:no-database-entry",
    [],
    u"There is no local database entry for a particular relative path in the magic folder.",
)

NOT_UPLOADING = MessageType(
    u"magic-folder:not-uploading",
    [],
    u"An item being processed is not going to be uploaded.",
)

SYMLINK = MessageType(
    u"magic-folder:symlink",
    [PATH],
    u"An item being processed was a symlink and is being skipped",
)

CREATED_DIRECTORY = Field.for_types(
    u"created_directory",
    [unicode],
    u"The relative path of a newly created directory in a magic-folder.",
)

PROCESS_DIRECTORY = ActionType(
    u"magic-folder:process-directory",
    [],
    [CREATED_DIRECTORY],
    u"An item being processed was a directory.",
)

DIRECTORY_PATHENTRY = MessageType(
    u"magic-folder:directory-dbentry",
    [magicfolderdb.PATHENTRY],
    u"Local database state relating to an item possibly being uploaded.",
)

NOT_NEW_DIRECTORY = MessageType(
    u"magic-folder:not-new-directory",
    [],
    u"A directory item being processed was found to not be new.",
)

NOT_NEW_FILE = MessageType(
    u"magic-folder:not-new-file",
    [],
    u"A file item being processed was found to not be new (or changed).",
)

SPECIAL_FILE = MessageType(
    u"magic-folder:special-file",
    [],
    u"An item being processed was found to be of a special type which is not supported.",
)

_COUNTER_NAME = Field.for_types(
    u"counter_name",
    # Should really only be unicode
    [unicode, bytes],
    u"The name of a counter.",
)

_DELTA = Field.for_types(
    u"delta",
    [int, long],
    u"An amount of a specific change in a counter.",
)

_VALUE = Field.for_types(
    u"value",
    [int, long],
    u"The new value of a counter after a change.",
)

COUNT_CHANGED = MessageType(
    u"magic-folder:count",
    [_COUNTER_NAME, _DELTA, _VALUE],
    u"The value of a counter has changed.",
)

START_MONITORING = ActionType(
    u"magic-folder:start-monitoring",
    [_NICKNAME, _DIRECTION],
    [],
    u"Uploader is beginning to monitor the filesystem for uploadable changes.",
)

STOP_MONITORING = ActionType(
    u"magic-folder:stop-monitoring",
    [_NICKNAME, _DIRECTION],
    [],
    u"Uploader is terminating filesystem monitoring operation.",
)

START_UPLOADING = ActionType(
    u"magic-folder:start-uploading",
    [_NICKNAME, _DIRECTION],
    [],
    u"Uploader is performing startup-time inspection of known files.",
)

_IGNORED = Field.for_types(
    u"ignored",
    [bool],
    u"A file proposed for queueing for processing is instead being ignored by policy.",
)

_ALREADY_PENDING = Field.for_types(
    u"already_pending",
    [bool],
    u"A file proposed for queueing for processing is already in the queue.",
)

_SIZE = Field.for_types(
    u"size",
    [int, long, type(None)],
    u"The size of a file accepted into the processing queue.",
)

ADD_PENDING = ActionType(
    u"magic-folder:add-pending",
    [eliotutil.RELPATH],
    [_IGNORED, _ALREADY_PENDING, _SIZE],
    u"Uploader is adding a path to the processing queue.",
)

FULL_SCAN = ActionType(
    u"magic-folder:full-scan",
    [_NICKNAME, _DIRECTION],
    [],
    u"A complete brute-force scan of the local directory is being performed.",
)

SCAN = ActionType(
    u"magic-folder:scan",
    [eliotutil.RELPATH],
    [],
    u"A brute-force scan of a subset of the local directory is being performed.",
)

NOTIFIED = ActionType(
    u"magic-folder:notified",
    [PATH, _NICKNAME, _DIRECTION],
    [],
    u"Magic-Folder received a notification of a local filesystem change for a certain path.",
)

_NON_DIR_CREATED = Field.for_types(
    u"non_dir_created",
    [bool],
    u"A creation event was for a non-directory and requires no further inspection.",
)


REACT_TO_INOTIFY = ActionType(
    u"magic-folder:react-to-inotify",
    [eliotutil.INOTIFY_EVENTS],
    [_IGNORED, _NON_DIR_CREATED, _ALREADY_PENDING],
    u"Magic-Folder is processing a notification from inotify(7) (or a clone) about a filesystem event.",
)

_ABSPATH = Field.for_types(
    u"abspath",
    [unicode],
    u"The absolute path of a file being written in a local directory.",
)

_IS_CONFLICT = Field.for_types(
    u"is_conflict",
    [bool],
    u"An indication of whether a file being written in a local directory is in a conflicted state.",
)

_NOW = Field.for_types(
    u"now",
    [int, long, float],
    u"The time at which a file is being written in a local directory.",
)

_MTIME = Field.for_types(
    u"mtime",
    [int, long, float, type(None)],
    u"A modification time to put into the metadata of a file being written in a local directory.",
)

WRITE_DOWNLOADED_FILE = ActionType(
    u"magic-folder:write-downloaded-file",
    [_ABSPATH, _SIZE, _IS_CONFLICT, _NOW, _MTIME],
    [],
    u"A downloaded file is being written to the filesystem.",
)

ALREADY_GONE = MessageType(
    u"magic-folder:rename:already-gone",
    [],
    u"A deleted file could not be rewritten to a backup path because it no longer exists.",
)

_REASON = Field(
    u"reason",
    lambda e: str(e),
    u"An exception which may describe the form of the conflict.",
    eliotutil.validateInstanceOf(Exception),
)

OVERWRITE_BECOMES_CONFLICT = MessageType(
    u"magic-folder:overwrite-becomes-conflict",
    [_REASON],
    u"An attempt to overwrite an existing file failed because that file is now conflicted.",
)

_FILES = Field(
    u"files",
    lambda file_set: list(file_set),
    u"All of the relative paths belonging to a Magic-Folder that are locally known.",
)

ALL_FILES = MessageType(
    u"magic-folder:all-files",
    [_FILES],
    u"A record of the rough state of the local database at the time of downloader start up.",
)

_ITEMS = Field(
    u"items",
    lambda deque: list(dict(relpath=item.relpath_u, kind=item.kind) for item in deque),
    u"Items in a processing queue.",
)

ITEM_QUEUE = MessageType(
    u"magic-folder:item-queue",
    [_ITEMS],
    u"A report of the items in the processing queue at this point.",
)

_BATCH = Field(
    u"batch",
    # Just report the paths for now.  Perhaps something from the values would
    # also be useful, though?  Consider it.
    lambda batch: batch.keys(),
    u"A batch of scanned items.",
    eliotutil.validateInstanceOf(dict),
)

SCAN_BATCH = MessageType(
    u"magic-folder:scan-batch",
    [_BATCH],
    u"Items in a batch of files which were scanned from the DMD.",
)

START_DOWNLOADING = ActionType(
    u"magic-folder:start-downloading",
    [_NICKNAME, _DIRECTION],
    [],
    u"A Magic-Folder downloader is initializing and beginning to manage downloads.",
)

PERFORM_SCAN = ActionType(
    u"magic-folder:perform-scan",
    [],
    [],
    u"Remote storage is being scanned for changes which need to be synchronized.",
)

_STATUS = Field.for_types(
    u"status",
    # Should just be unicode...
    [unicode, bytes],
    u"The status of an item in a processing queue.",
)

QUEUED_ITEM_STATUS_CHANGE = MessageType(
    u"magic-folder:item:status-change",
    [eliotutil.RELPATH, _STATUS],
    u"A queued item changed status.",
)

_CONFLICT_REASON = Field.for_types(
    u"conflict_reason",
    [unicode, type(None)],
    u"A human-readable explanation of why a file was in conflict.",
    eliotutil.validateSetMembership({
        u"dbentry mismatch metadata",
        u"dbentry newer version",
        u"last_downloaded_uri mismatch",
        u"file appeared",
        None,
    }),
)

CHECKING_CONFLICTS = ActionType(
    u"magic-folder:item:checking-conflicts",
    [],
    [_IS_CONFLICT, _CONFLICT_REASON],
    u"A potential download item is being checked to determine if it is in a conflicted state.",
)

REMOTE_DIRECTORY_CREATED = MessageType(
    u"magic-folder:remote-directory-created",
    [],
    u"The downloader found a new directory in the DMD.",
)

REMOTE_DIRECTORY_DELETED = MessageType(
    u"magic-folder:remote-directory-deleted",
    [],
    u"The downloader found a directory has been deleted from the DMD.",
)

class QueueMixin(HookMixin):
    """
    A parent class for Uploader and Downloader that handles putting
    IQueuedItem instances into a work queue and processing
    them. Tracks some history of recent items processed (for the
    "status" API).

    Subclasses implement _scan_delay, _perform_scan and _process

    :ivar unicode _name: Either "uploader" or "downloader".

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
        self._log_fields = dict(
            nickname=self._client.nickname,
            direction=self._name,
        )
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
        return extend_filepath(self._local_filepath, relpath_u.split(u"/"))

    def stop(self):
        """
        Don't process queued items anymore.

        :return Deferred: A ``Deferred`` that fires when processing has
            completely stopped.
        """
        d = self._processing
        self._processing_loop.stop()
        self._processing = None
        self._processing_loop = None
        return d

    def _begin_processing(self):
        """
        Start a loop that looks for work to do and then does it.
        """
        action = PROCESSING_LOOP(**self._log_fields)

        # Note that we don't put the processing iterations into the logging
        # action because we expect this loop to run for the whole lifetime of
        # the process.  The tooling for dealing with incomplete action trees
        # is still somewhat lacking.  Putting the iteractions into the overall
        # loop action would hamper reading those logs for now.
        self._processing_loop = task.LoopingCall(self._processing_iteration)
        self._processing_loop.clock = self._clock
        self._processing = self._processing_loop.start(self._scan_delay(), now=True)

        with action.context():
            # We do make sure errors appear in the loop action though.
            d = DeferredContext(self._processing)
            d.addActionFinish()

    def _processing_iteration(self):
        """
        One iteration runs self._process_deque which calls _perform_scan() and
        then completely drains the _deque (processing each item).
        """
        action = ITERATION(**self._log_fields)
        with action.context():
            d = DeferredContext(defer.Deferred())

            # adds items to our deque
            d.addCallback(lambda ignored: self._perform_scan())

            # process anything in our queue
            d.addCallback(lambda ignored: self._process_deque())

            # Let the tests know we've made it this far.
            d.addCallback(lambda ignored: self._call_hook(None, 'iteration'))

            # Get it out of the Eliot context
            result = d.addActionFinish()

            # Kick it off
            result.callback(None)

            # Give it back to LoopingCall so it can wait on us.
            return result

    def _scan_delay(self):
        raise NotImplementedError

    def _perform_scan(self):
        return

    @eliotutil.inline_callbacks
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

        with PROCESS_QUEUE(count=len(to_process)):
            for item in to_process:
                self._process_history.appendleft(item)
                self._in_progress.remove(item)
                try:
                    proc = yield self._process(item)
                    if not proc:
                        self._process_history.remove(item)
                    self._call_hook(item, 'item_processed')
                except:
                    write_traceback()
                    item.set_status('failed', self._clock.seconds())
                    proc = Failure()

                self._call_hook(proc, 'processed')

    def _get_relpath(self, filepath):
        segments = unicode_segments_from(filepath, self._local_filepath)
        return u"/".join(segments)

    def _count(self, counter_name, delta=1):
        ctr = 'magic_folder.%s.%s' % (self._name, counter_name)
        self._client.stats_provider.count(ctr, delta)
        COUNT_CHANGED.log(
            counter_name=counter_name,
            delta=delta,
            value=self._client.stats_provider.counters[ctr],
        )

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
    kind = None

    def __init__(self, relpath_u, progress, size):
        self.relpath_u = relpath_u
        self.progress = progress
        self._status_history = dict()
        self.size = size

    def set_status(self, status, current_time=None):
        if current_time is None:
            current_time = time.time()
        self._status_history[status] = current_time
        QUEUED_ITEM_STATUS_CHANGE.log(
            relpath=self.relpath_u,
            status=status,
        )

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
    kind = u"upload"


_ITEM = Field(
    u"item",
    lambda i: {
        u"relpath": i.relpath_u,
        u"size": i.size,
    },
    u"An item to be uploaded or downloaded.",
    eliotutil.validateInstanceOf(QueuedItem),
)

PROCESS_ITEM = ActionType(
    u"magic-folder:process-item",
    [_ITEM],
    [],
    u"A path which was found wanting of an update is receiving an update.",
)

DOWNLOAD_BEST_VERSION = ActionType(
    u"magic-folder:download-best-version",
    [],
    [],
    u"The content of a file in the Magic Folder is being downloaded.",
)

class Uploader(QueueMixin):

    def __init__(self, client, local_path_u, db, upload_dirnode, pending_delay, clock):
        QueueMixin.__init__(self, client, local_path_u, db, u'uploader', clock)

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

    def _add_watch(self):
        self._notifier.watch(
            self._local_filepath,
            mask=self.mask,
            callbacks=[self._notify],
            recursive=True,
        )

    def start_monitoring(self):
        action = START_MONITORING(**self._log_fields)
        with action.context():
            d = DeferredContext(defer.succeed(None))

        d.addCallback(lambda ign: self._notifier.startReading())
        d.addCallback(lambda ign: self._add_watch())
        d.addCallback(lambda ign: self._count('dirs_monitored'))
        d.addBoth(self._call_hook, 'started')
        return d.addActionFinish()

    def stop(self):
        action = STOP_MONITORING(**self._log_fields)
        with action.context():
            self._notifier.stopReading()
            self._count('dirs_monitored', -1)
            if self._periodic_callid:
                try:
                    self._periodic_callid.cancel()
                except AlreadyCancelled:
                    pass

            if hasattr(self._notifier, 'wait_until_stopped'):
                d = DeferredContext(self._notifier.wait_until_stopped())
            else:
                d = DeferredContext(defer.succeed(None))

            d.addCallback(lambda ignored: QueueMixin.stop(self))
            return d.addActionFinish()

    def start_uploading(self):
        action = START_UPLOADING(**self._log_fields)
        with action:
            self.is_ready = True

            all_relpaths = self._db.get_all_relpaths()

            for relpath_u in all_relpaths:
                self._add_pending(relpath_u)

            self._full_scan()
            self._begin_processing()

    def _scan_delay(self):
        return self._pending_delay

    def _full_scan(self):
        with FULL_SCAN(**self._log_fields):
            self._periodic_callid = self._clock.callLater(self._periodic_full_scan_duration, self._full_scan)
            self._scan(u"")

    def _add_pending(self, relpath_u):
        with ADD_PENDING(relpath=relpath_u) as action:
            if magicpath.should_ignore_file(relpath_u):
                action.add_success_fields(ignored=True, already_pending=False, size=None)
                return
            if self.is_pending(relpath_u):
                action.add_success_fields(ignored=False, already_pending=True, size=None)
                return

            self._pending.add(relpath_u)
            fp = self._get_filepath(relpath_u)
            pathinfo = get_pathinfo(unicode_from_filepath(fp))
            progress = PercentProgress()
            action.add_success_fields(ignored=False, already_pending=False, size=pathinfo.size)
            item = UploadItem(relpath_u, progress, pathinfo.size)
            item.set_status('queued', self._clock.seconds())
            self._deque.append(item)
            self._count('objects_queued')

    def _scan(self, reldir_u):
        # Scan a directory by (synchronously) adding the paths of all its children to self._pending.
        # Note that this doesn't add them to the deque -- that will
        with SCAN(relpath=reldir_u):
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
                _assert(isinstance(child, unicode), child=child)
                self._add_pending("%s/%s" % (reldir_u, child) if reldir_u != u"" else child)

    def is_pending(self, relpath_u):
        return relpath_u in self._pending

    def _notify(self, opaque, path, events_mask):
        with NOTIFIED(path=path, **self._log_fields):
            try:
                return self._real_notify(opaque, path, events_mask)
            except Exception:
                write_traceback()

    def _real_notify(self, opaque, path, events_mask):
        action = REACT_TO_INOTIFY(
            # We could think about logging opaque here but ... it's opaque.
            # All can do is id() or repr() it and neither of those actually
            # produces very illuminating results.  We drop opaque on the
            # floor, anyway.
            inotify_events=events_mask,
        )
        success_fields = dict(non_dir_created=False, already_pending=False, ignored=False)

        with action:
            relpath_u = self._get_relpath(path)

            # We filter out IN_CREATE events not associated with a directory.
            # Acting on IN_CREATE for files could cause us to read and upload
            # a possibly-incomplete file before the application has closed it.
            # There should always be an IN_CLOSE_WRITE after an IN_CREATE, I think.
            # It isn't possible to avoid watching for IN_CREATE at all, because
            # it is the only event notified for a directory creation.

            if ((events_mask & self._inotify.IN_CREATE) != 0 and
                (events_mask & self._inotify.IN_ISDIR) == 0):
                success_fields[u"non_dir_created"] = True
            elif relpath_u in self._pending:
                success_fields[u"already_pending"] = True
            elif magicpath.should_ignore_file(relpath_u):
                success_fields[u"ignored"] = True
            else:
                self._add_pending(relpath_u)

            # Always fire the inotify hook.  If an accident of timing causes a
            # second inotify event for a particular path before the first has
            # been processed, the expectation is still that any code that was
            # waiting for the second inotify event should be notified.
            self._call_hook(path, 'inotify')
            action.add_success_fields(**success_fields)

    def _process(self, item):
        """
        Possibly upload a single QueuedItem.  If this returns False, the item is
        removed from _process_history.
        """
        # Uploader
        with PROCESS_ITEM(item=item).context():
            d = DeferredContext(defer.succeed(False))

            relpath_u = item.relpath_u
            item.set_status('started', self._clock.seconds())

            if relpath_u is None:
                item.set_status('invalid_path', self._clock.seconds())
                return d.addActionFinish()

            precondition(isinstance(relpath_u, unicode), relpath_u)
            precondition(not relpath_u.endswith(u'/'), relpath_u)

        def _maybe_upload(ign, now=None):
            MAYBE_UPLOAD.log(relpath=relpath_u)
            if now is None:
                now = time.time()
            fp = self._get_filepath(relpath_u)
            pathinfo = get_pathinfo(unicode_from_filepath(fp))

            try:
                with REMOVE_FROM_PENDING(relpath=relpath_u, pending=self._pending):
                    self._pending.remove(relpath_u)
            except KeyError:
                pass
            encoded_path_u = magicpath.path2magic(relpath_u)

            if not pathinfo.exists:
                # FIXME merge this with the 'isfile' case.
                NOTIFIED_OBJECT_DISAPPEARED.log(path=fp)
                self._count('objects_disappeared')

                if pathinfo.isdir:
                    with PROPAGATE_DIRECTORY_DELETION():
                        for localpath in self._db.get_direct_children(relpath_u):
                            self._add_pending(localpath.relpath_u)

                db_entry = self._db.get_db_entry(relpath_u)
                if db_entry is None:
                    NO_DATABASE_ENTRY.log()
                    return False

                last_downloaded_timestamp = now  # is this correct?

                if is_new_file(pathinfo, db_entry):
                    new_version = db_entry.version + 1
                else:
                    NOT_UPLOADING.log()
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
                d2 = DeferredContext(self._upload_dirnode.add_file(
                    encoded_path_u, empty_uploadable,
                    metadata=metadata,
                    overwrite=True,
                    progress=item.progress,
                ))

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
                return d2.result
            elif pathinfo.islink:
                SYMLINK.log(path=fp)
                return False
            elif pathinfo.isdir:
                if not getattr(self._notifier, 'recursive_includes_new_subdirectories', False):
                    self._notifier.watch(fp, mask=self.mask, callbacks=[self._notify], recursive=True)

                db_entry = self._db.get_db_entry(relpath_u)
                DIRECTORY_PATHENTRY.log(pathentry=db_entry)
                if not is_new_file(pathinfo, db_entry):
                    NOT_NEW_DIRECTORY.log()
                    return False

                uploadable = Data("", self._client.convergence)
                encoded_path_u += magicpath.path2magic(u"/")
                with PROCESS_DIRECTORY().context() as action:
                    upload_d = DeferredContext(self._upload_dirnode.add_file(
                        encoded_path_u, uploadable,
                        metadata={"version": 0},
                        overwrite=True,
                        progress=item.progress,
                    ))
                def _dir_succeeded(ign):
                    action.add_success_fields(created_directory=relpath_u)
                    self._count('directories_created')
                upload_d.addCallback(_dir_succeeded)
                upload_d.addCallback(lambda ign: self._scan(relpath_u))
                upload_d.addCallback(lambda ign: True)
                return upload_d.addActionFinish()
            elif pathinfo.isfile:
                db_entry = self._db.get_db_entry(relpath_u)

                last_downloaded_timestamp = now

                if db_entry is None:
                    new_version = 0
                elif is_new_file(pathinfo, db_entry):
                    new_version = db_entry.version + 1
                else:
                    NOT_NEW_FILE.log()
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
                d2 = DeferredContext(self._upload_dirnode.add_file(
                    encoded_path_u, uploadable,
                    metadata=metadata,
                    overwrite=True,
                    progress=item.progress,
                ))

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
                return d2.result
            else:
                SPECIAL_FILE.log()
                return False

        d.addCallback(_maybe_upload)

        def _succeeded(res):
            if res:
                self._count('objects_succeeded')
            # TODO: maybe we want the status to be 'ignored' if res is False
            item.set_status('success', self._clock.seconds())
            return res
        def _failed(f):
            self._count('objects_failed')
            item.set_status('failure', self._clock.seconds())
            return f
        d.addCallbacks(_succeeded, _failed)
        return d.addActionFinish()

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
        if now is None:
            now = time.time()
        action = WRITE_DOWNLOADED_FILE(
            abspath=abspath_u,
            size=len(file_contents),
            is_conflict=is_conflict,
            now=now,
            mtime=mtime,
        )
        with action:
            return self._write_downloaded_file_logged(
                local_path_u,
                abspath_u,
                file_contents,
                is_conflict,
                now,
                mtime,
            )

    def _write_downloaded_file_logged(self, local_path_u, abspath_u,
                                      file_contents, is_conflict, now, mtime):
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
                OVERWRITE_BECOMES_CONFLICT.log(reason=e)
                return self._rename_conflicted_file(abspath_u, replacement_path_u)

    @log_call(
        action_type=u"magic-folder:rename-conflicted",
        include_args=["abspath_u", "replacement_path_u"],
    )
    def _rename_conflicted_file(self, abspath_u, replacement_path_u):
        conflict_path_u = self._get_conflicted_filename(abspath_u)
        fileutil.rename_no_overwrite(replacement_path_u, conflict_path_u)
        return conflict_path_u

    @log_call(
        action_type=u"magic-folder:rename-deleted",
        include_args=["abspath_u"],
    )
    def _rename_deleted_file(self, abspath_u):
        try:
            fileutil.rename_no_overwrite(abspath_u, abspath_u + u'.backup')
        except OSError:
            ALREADY_GONE.log()
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
    kind = u"download"

    def __init__(self, relpath_u, progress, filenode, metadata, size):
        super(DownloadItem, self).__init__(relpath_u, progress, size)
        self.file_node = filenode
        self.metadata = metadata


class Downloader(QueueMixin, WriteFileMixin):

    def __init__(self, client, local_path_u, db, collective_dirnode,
                 upload_readonly_dircap, clock, is_upload_pending, umask,
                 status_reporter, poll_interval=60):
        QueueMixin.__init__(self, client, local_path_u, db, u'downloader', clock)

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

    @eliotutil.inline_callbacks
    def start_downloading(self):
        action = START_DOWNLOADING(**self._log_fields)
        with action:
            ALL_FILES.log(files=self._db.get_all_relpaths())

            while True:
                try:
                    yield self._scan_remote_collective(scan_self=True)
                    # The integration tests watch for this log message to
                    # decide when it is safe to proceed.  Clearly, we need
                    # better programmatic interrogation of magic-folder state.
                    print("Completed initial Magic Folder scan successfully ({})".format(self))
                    self._begin_processing()
                    return
                except Exception:
                    self._status_reporter(
                        False, "Initial scan has failed",
                        "Last tried at %s" % self.nice_current_time(),
                    )
                    write_traceback()
                    yield task.deferLater(self._clock, self._poll_interval, lambda: None)

    def nice_current_time(self):
        return format_time(datetime.fromtimestamp(self._clock.seconds()).timetuple())

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
        action = start_action(
            action_type=u"magic-folder:downloader:get-latest-file",
            name=filename,
        )
        with action.context():
            collective_dirmap_d = DeferredContext(self._collective_dirnode.list())
        def scan_collective(result):
            Message.log(
                message_type=u"magic-folder:downloader:get-latest-file:collective-scan",
                dmds=result.keys(),
            )
            list_of_deferreds = []
            for dir_name in result.keys():
                # XXX make sure it's a directory
                d = DeferredContext(defer.succeed(None))
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
                    Message.log(
                        message_type=u"magic-folder:downloader:get-latest-file:version",
                        version=result[1]['version'],
                    )
                    if node is None or result[1]['version'] > max_version:
                        node, metadata = result
                        max_version = result[1]['version']
                else:
                    write_traceback()
            return node, metadata
        collective_dirmap_d.addCallback(highest_version)
        return collective_dirmap_d.addActionFinish()

    def _scan_remote_dmd(self, nickname, dirnode, scan_batch):
        with SCAN_REMOTE_DMD(nickname=nickname).context():
            d = DeferredContext(dirnode.list())
        def scan_listing(listing_map):
            for encoded_relpath_u in listing_map.keys():
                relpath_u = magicpath.magic2path(encoded_relpath_u)

                file_node, metadata = listing_map[encoded_relpath_u]
                local_dbentry = self._get_local_latest(relpath_u)

                # XXX FIXME this is *awefully* similar to
                # _should_download code in function etc -- can we
                # share?
                remote_version = metadata.get('version', None)
                remote_uri = file_node.get_readonly_uri()
                REMOTE_DMD_ENTRY.log(
                    relpath=relpath_u,
                    pathentry=local_dbentry,
                    remote_version=remote_version,
                    remote_uri=remote_uri,
                )

                if (local_dbentry is None or remote_version is None or
                    local_dbentry.version < remote_version or
                    (local_dbentry.version == remote_version and local_dbentry.last_downloaded_uri != remote_uri)):
                    ADD_TO_DOWNLOAD_QUEUE.log(relpath=relpath_u)
                    if scan_batch.has_key(relpath_u):
                        scan_batch[relpath_u] += [(file_node, metadata)]
                    else:
                        scan_batch[relpath_u] = [(file_node, metadata)]
            self._status_reporter(
                True, 'Magic folder is working',
                'Last scan: %s' % self.nice_current_time(),
            )

        d.addCallback(scan_listing)
        return d.addActionFinish()

    @eliotutil.log_call_deferred(SCAN_REMOTE_COLLECTIVE.action_type)
    def _scan_remote_collective(self, scan_self=False):
        scan_batch = {}  # path -> [(filenode, metadata)]
        d = DeferredContext(self._collective_dirnode.list())
        def scan_collective(dirmap):
            d2 = DeferredContext(defer.succeed(None))
            for dir_name in dirmap:
                (dirnode, metadata) = dirmap[dir_name]
                if scan_self or dirnode.get_readonly_uri() != self._upload_readonly_dircap:
                    d2.addCallback(lambda ign, dir_name=dir_name, dirnode=dirnode:
                                   self._scan_remote_dmd(dir_name, dirnode, scan_batch))
                    # XXX what should we do to make this failure more visible to users?
                    d2.addErrback(write_traceback)
            return d2.result
        d.addCallback(scan_collective)

        @log_call(
            action_type=u"magic-folder:filter-batch-to-deque",
            include_args=[],
            include_result=False,
        )
        def _filter_batch_to_deque(ign):
            ITEM_QUEUE.log(items=self._deque)
            SCAN_BATCH.log(batch=scan_batch)
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
                    self._call_hook(None, 'processed', async=True)  # await this maybe-Deferred??

        d.addCallback(_filter_batch_to_deque)
        return d.result

    def _scan_delay(self):
        return self._poll_interval

    @eliotutil.log_call_deferred(PERFORM_SCAN.action_type)
    @eliotutil.inline_callbacks
    def _perform_scan(self):
        try:
            yield self._scan_remote_collective()
            self._status_reporter(
                True, 'Magic folder is working',
                'Last scan: %s' % self.nice_current_time(),
            )
        except Exception as e:
            write_traceback()
            self._status_reporter(
                False, 'Remote scan has failed: %s' % str(e),
                'Last attempted at %s' % self.nice_current_time(),
            )

    def _process(self, item):
        """
        Possibly upload a single QueuedItem.  If this returns False, the item is
        removed from _process_history.
        """
        # Downloader
        now = self._clock.seconds()

        item.set_status('started', now)
        fp = self._get_filepath(item.relpath_u)
        abspath_u = unicode_from_filepath(fp)
        conflict_path_u = self._get_conflicted_filename(abspath_u)
        last_uploaded_uri = item.metadata.get('last_uploaded_uri', None)

        with PROCESS_ITEM(item=item):
            d = DeferredContext(defer.succeed(False))

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

            with CHECKING_CONFLICTS() as action:
                conflict_reason = None
                if db_entry:
                    # * 2c. If any of the following are true, then classify as a conflict:
                    #   * i. there are pending notifications of changes to ``foo``;
                    #   * ii. the last-seen statinfo is either absent (i.e. there is
                    #     no entry in the database for this path), or different from the
                    #     current statinfo;

                    if current_statinfo.exists:
                        if (db_entry.mtime_ns != current_statinfo.mtime_ns or \
                            db_entry.ctime_ns != current_statinfo.ctime_ns or \
                            db_entry.size != current_statinfo.size):
                            is_conflict = True
                            conflict_reason = u"dbentry mismatch metadata"

                        if db_entry.last_downloaded_uri is None \
                           or db_entry.last_uploaded_uri is None \
                           or dmd_last_downloaded_uri is None:
                            # we've never downloaded anything before for this
                            # file, but the other side might have created a new
                            # file "at the same time"
                            if db_entry.version >= item.metadata['version']:
                                is_conflict = True
                                conflict_reason = u"dbentry newer version"
                        elif dmd_last_downloaded_uri != db_entry.last_downloaded_uri:
                            is_conflict = True
                            conflict_reason = u"last_downloaded_uri mismatch"

                else:  # no local db_entry .. but has the file appeared locally meantime?
                    if current_statinfo.exists:
                        is_conflict = True
                        conflict_reason = u"file appeared"

                action.add_success_fields(
                    is_conflict=is_conflict,
                    conflict_reason=conflict_reason,
                )

            if is_conflict:
                self._count('objects_conflicted')

            if item.relpath_u.endswith(u"/"):
                if item.metadata.get('deleted', False):
                    REMOTE_DIRECTORY_DELETED.log()
                else:
                    REMOTE_DIRECTORY_CREATED.log()
                    d.addCallback(lambda ign: fileutil.make_dirs(abspath_u))
                    d.addCallback(lambda ign: abspath_u)
            else:
                if item.metadata.get('deleted', False):
                    d.addCallback(lambda ign: self._rename_deleted_file(abspath_u))
                else:
                    @eliotutil.log_call_deferred(DOWNLOAD_BEST_VERSION.action_type)
                    def download_best_version(ignored):
                        d = DeferredContext(item.file_node.download_best_version(progress=item.progress))
                        d.addCallback(lambda contents: self._write_downloaded_file(
                            self._local_path_u, abspath_u, contents,
                            is_conflict=is_conflict,
                            mtime=item.metadata.get('user_mtime', item.metadata.get('tahoe', {}).get('linkmotime')),
                        ))
                        return d.result

                    d.addCallback(download_best_version)

        d.addCallback(do_update_db)
        d.addErrback(failed)

        def trap_conflicts(f):
            f.trap(ConflictError)
            return False
        d.addErrback(trap_conflicts)
        return d.addActionFinish()
