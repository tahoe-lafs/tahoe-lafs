#
# this updates the (tagged) version of the software
#
# Any "options" are hard-coded in here (e.g. the GnuPG key to use)
#

author = "meejah <meejah@meejah.ca>"


import sys
import time
from datetime import datetime
from packaging.version import Version

from dulwich.repo import Repo
from dulwich.porcelain import (
    tag_list,
    tag_create,
    status,
)

from twisted.internet.task import (
    react,
)
from twisted.internet.defer import (
    ensureDeferred,
)


def existing_tags(git):
    versions = sorted(
        Version(v.decode("utf8").lstrip("tahoe-lafs-"))
        for v in tag_list(git)
        if v.startswith(b"tahoe-lafs-")
    )
    return versions


def create_new_version(git):
    versions = existing_tags(git)
    biggest = versions[-1]

    return Version(
        "{}.{}.{}".format(
            biggest.major,
            biggest.minor + 1,
            0,
        )
    )


async def main(reactor):
    git = Repo(".")

    st = status(git)
    if any(st.staged.values()) or st.unstaged:
        print("unclean checkout; aborting")
        raise SystemExit(1)

    v = create_new_version(git)
    if "--no-tag" in sys.argv:
        print(v)
        return

    print("Existing tags: {}".format("\n".join(str(x) for x in existing_tags(git))))
    print("New tag will be {}".format(v))

    # the "tag time" is seconds from the epoch .. we quantize these to
    # the start of the day in question, in UTC.
    now = datetime.now()
    s = now.utctimetuple()
    ts = int(
        time.mktime(
            time.struct_time((s.tm_year, s.tm_mon, s.tm_mday, 0, 0, 0, 0, s.tm_yday, 0))
        )
    )
    tag_create(
        repo=git,
        tag="tahoe-lafs-{}".format(str(v)).encode("utf8"),
        author=author.encode("utf8"),
        message="Release {}".format(v).encode("utf8"),
        annotated=True,
        objectish=b"HEAD",
        sign=author.encode("utf8"),
        tag_time=ts,
        tag_timezone=0,
    )

    print("Tag created locally, it is not pushed")
    print("To push it run something like:")
    print("   git push origin {}".format(v))


if __name__ == "__main__":
    react(lambda r: ensureDeferred(main(r)))
