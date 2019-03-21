
from __future__ import (
    print_function,
    unicode_literals,
    absolute_import,
    division,
)

from twisted.web.resource import (
    Resource,
)

from .logs import (
    create_log_resources,
)

def create_private_tree(client):
    private = Resource()
    private.putChild(b"logs", create_log_resources(client))
    return private
