# -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-
# -*- indent-tabs-mode: nil -*-


from foolscap import base32
def nodeid_b2a(nodeid):
    # we display nodeids using the same base32 alphabet that Foolscap uses
    return base32.encode(nodeid)

def shortnodeid_b2a(nodeid):
    return nodeid_b2a(nodeid)[:8]
