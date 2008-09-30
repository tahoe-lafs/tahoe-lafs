
# put this definition in a separate file, because axiom uses the
# fully-qualified classname as a database table name, so __builtin__ is kinda
# ugly.

from axiom.item import Item
from axiom.attributes import text, integer, timestamp

class Sample(Item):
    url = text(indexed=True)
    when = timestamp(indexed=True)
    used = integer()
    avail = integer()

