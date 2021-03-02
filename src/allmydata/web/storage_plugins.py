"""
This module implements a resource which has as children the web resources
of all enabled storage client plugins.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from twisted.web.resource import (
    Resource,
    NoResource,
)

class StoragePlugins(Resource, object):
    """
    The parent resource of all enabled storage client plugins' web resources.
    """
    def __init__(self, client):
        """
        :param _Client client: The Tahoe-LAFS client node object which will be
            used to find the storage plugin web resources.
        """
        Resource.__init__(self)
        self._client = client

    def getChild(self, segment, request):
        """
        Get an ``IResource`` from the loaded, enabled plugin with a name that
        equals ``segment``.

        :see: ``twisted.web.iweb.IResource.getChild``
        """
        resources = self._client.get_client_storage_plugin_web_resources()
        try:
            # Technically client could be using some other encoding?
            result = resources[segment.decode("utf-8")]
        except KeyError:
            result = NoResource()
        self.putChild(segment, result)
        return result
