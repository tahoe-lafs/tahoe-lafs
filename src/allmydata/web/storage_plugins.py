"""
This module implements a resource which has as children the web resources
of all enabled storage client plugins.
"""

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
            result = resources[segment]
        except KeyError:
            result = NoResource()
        self.putChild(segment, result)
        return result
