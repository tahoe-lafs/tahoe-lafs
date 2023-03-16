
import psutil
import filelock


def can_spawn_tahoe(pidfile):
    """
    Determine if we can spawn a Tahoe-LAFS for the given pidfile. That
    pidfile may be deleted if it is stale.

    :param pathlib.Path pidfile: the file to check, that is the Path
        to "running.process" in a Tahoe-LAFS configuration directory

    :returns bool: True if we can spawn `tahoe run` here
    """
    lockpath = pidfile.parent / (pidfile.name + ".lock")
    with filelock.FileLock(lockpath):
        try:
            with pidfile.open("r") as f:
                pid, create_time = f.read().strip().split(" ", 1)
        except FileNotFoundError:
            return True

        # somewhat interesting: we have a pidfile
        pid = int(pid)
        create_time = float(create_time)

        try:
            proc = psutil.Process(pid)
            # most interesting case: there _is_ a process running at the
            # recorded PID -- but did it just happen to get that PID, or
            # is it the very same one that wrote the file?
            if create_time == proc.create_time():
                # _not_ stale! another intance is still running against
                # this configuration
                return False

        except psutil.NoSuchProcess:
            pass

        # the file is stale
        pidfile.unlink()
        return True


from pathlib import Path
print("can spawn?", can_spawn_tahoe(Path("running.process")))
