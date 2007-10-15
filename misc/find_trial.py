import sys

from allmydata.util import find_exe

if __name__ == "__main__":
    cmd = find_exe.find_exe("trial")
    if cmd:
        print " ".join(cmd).replace("\\", "/")
    else:
        sys.exit(1)
