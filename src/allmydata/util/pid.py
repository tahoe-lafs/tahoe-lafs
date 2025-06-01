"""
Process IDentification-related helpers.
"""

from __future__ import annotations

import psutil

# the docs are a little misleading, but this is either WindowsFileLock
# or UnixFileLock depending upon the platform we're currently on
from filelock import FileLock, Timeout

from twisted.python.filepath import FilePath


class ProcessInTheWay(Exception):
    """
    our pidfile points at a running process
    """


class InvalidPidFile(Exception):
    """
    our pidfile isn't well-formed
    """


class CannotRemovePidFile(Exception):
    """
    something went wrong removing the pidfile
    """


def _pidfile_to_lockpath(pidfile: FilePath) -> FilePath:
    """
    internal helper.
    :returns: a path to use for file-locking the given pidfile
    """
    return pidfile.sibling("{}.lock".format(pidfile.basename()))


def parse_pidfile(pidfile: FilePath) -> tuple[int, float]:
    """
    :param pidfile: The path to the file to parse.

    :return: 2-tuple of pid, creation-time as int, float

    :raises InvalidPidFile: on error
    """
    with pidfile.open("r") as f:
        content = f.read().decode("utf8").strip()
    try:
        pid_str, starttime_str = content.split()
        pid = int(pid_str)
        starttime = float(starttime_str)
    except ValueError:
        raise InvalidPidFile(
            "found invalid PID file in {}".format(
                pidfile
            )
        )
    if pid <= 0 or starttime < 0:
        raise InvalidPidFile(f"Found value out of bounds: pid={pid} time={starttime}")
    return pid, starttime


def check_pid_process(pidfile: FilePath) -> None:
    """
    If another instance appears to be running already, raise an
    exception.  Otherwise, write our PID + start time to the pidfile
    and arrange to delete it upon exit.

    :param FilePath pidfile: the file to read/write our PID from.

    :raises ProcessInTheWay: if a running process exists at our PID
    """
    lock_path = _pidfile_to_lockpath(pidfile)

    try:
        # a short timeout is fine, this lock should only be active
        # while someone is reading or deleting the pidfile .. and
        # facilitates testing the locking itself.
        with FileLock(lock_path.path, timeout=2):
            # check if we have another instance running already
            if pidfile.exists():
                pid, starttime = parse_pidfile(pidfile)
                try:
                    # if any other process is running at that PID, let the
                    # user decide if this is another legitimate
                    # instance. Automated programs may use the start-time to
                    # help decide this (if the PID is merely recycled, the
                    # start-time won't match).
                    psutil.Process(pid)
                    raise ProcessInTheWay(
                        "A process is already running as PID {}".format(pid)
                    )
                except psutil.NoSuchProcess:
                    print(
                        "'{pidpath}' refers to {pid} that isn't running".format(
                            pidpath=pidfile.asTextMode().path,
                            pid=pid,
                        )
                    )
                    # nothing is running at that PID so it must be a stale file
                    pidfile.remove()

            # write our PID + start-time to the pid-file
            proc = psutil.Process()
            with pidfile.open("w") as f:
                f.write("{} {}\n".format(proc.pid, proc.create_time()).encode("utf8"))
    except Timeout:
        raise ProcessInTheWay(
            "Another process is still locking {}".format(pidfile.asTextMode().path)
        )


def cleanup_pidfile(pidfile: FilePath) -> None:
    """
    Remove the pidfile specified (respecting locks). If anything at
    all goes wrong, `CannotRemovePidFile` is raised.
    """
    lock_path = _pidfile_to_lockpath(pidfile)
    with FileLock(lock_path.path):
        try:
            pidfile.remove()
        except Exception as e:
            raise CannotRemovePidFile(
                "Couldn't remove '{pidfile}': {err}.".format(
                    pidfile=pidfile.asTextMode().path,
                    err=e,
                )
            )
