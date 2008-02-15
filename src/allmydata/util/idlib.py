

from foolscap import base32
def nodeid_b2a(nodeid):
    # we display nodeids using the same base32 alphabet that Foolscap uses
    return base32.encode(nodeid)

def shortnodeid_b2a(nodeid):
    return nodeid_b2a(nodeid)[:8]
