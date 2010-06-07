
from axiom.item import Item
from axiom.attributes import text, integer, timestamp


class Sample(Item):
    # we didn't originally set typeName, so it was generated from the
    # fully-qualified classname ("diskwatcher.Sample"), then Axiom
    # automatically lowercases and un-dot-ifies it to get
    # "diskwatcher_sample". Now we explicitly provide a name.
    typeName = "diskwatcher_sample"

    # version 2 added the 'total' field
    schemaVersion = 2

    url = text(indexed=True)
    when = timestamp(indexed=True)
    total = integer()
    used = integer()
    avail = integer()

def upgradeSample1to2(old):
    total = 0
    return old.upgradeVersion("diskwatcher_sample", 1, 2,
                              url=old.url,
                              when=old.when,
                              total=0,
                              used=old.used,
                              avail=old.avail)

from axiom.upgrade import registerUpgrader
registerUpgrader(upgradeSample1to2, "diskwatcher_sample", 1, 2)
