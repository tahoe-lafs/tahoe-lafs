import os
import psutil


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


def check_pid_process(pidfile, find_process=None):
    """
    If another instance appears to be running already, raise an
    exception.  Otherwise, write our PID + start time to the pidfile
    and arrange to delete it upon exit.

    :param FilePath pidfile: the file to read/write our PID from.

    :param Callable find_process: None, or a custom way to get a
        Process objet (usually for tests)

    :raises ProcessInTheWay: if a running process exists at our PID
    """
    find_process = psutil.Process if find_process is None else find_process
    # check if we have another instance running already
    if pidfile.exists():
        with pidfile.open("r") as f:
            content = f.read().decode("utf8").strip()
        try:
            pid, starttime = content.split()
            pid = int(pid)
            starttime = float(starttime)
        except ValueError:
            raise InvalidPidFile(
                "found invalid PID file in {}".format(
                    pidfile
                )
            )
        try:
            # if any other process is running at that PID, let the
            # user decide if this is another legitimate
            # instance. Automated programs may use the start-time to
            # help decide this (if the PID is merely recycled, the
            # start-time won't match).
            find_process(pid)
            raise ProcessInTheWay(
                "A process is already running as PID {}".format(pid)
            )
        except psutil.NoSuchProcess:
            print(
                "'{pidpath}' refers to {pid} that isn't running".format(
                    pidpath=pidfile.path,
                    pid=pid,
                )
            )
            # nothing is running at that PID so it must be a stale file
            pidfile.remove()

    # write our PID + start-time to the pid-file
    pid = os.getpid()
    starttime = find_process(pid).create_time()
    with pidfile.open("w") as f:
        f.write("{} {}\n".format(pid, starttime).encode("utf8"))


def cleanup_pidfile(pidfile):
    """
    Safely clean up a PID-file
    """
    try:
        pidfile.remove()
    except Exception as e:
        raise CannotRemovePidFile(
            "Couldn't remove '{pidfile}': {err}.".format(
                pidfile=pidfile.path,
                err=e,
            )
        )
