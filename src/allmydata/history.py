
import weakref
from twisted.application import service

class History(service.Service):
    """Keep track of recent operations, for a status display."""

    name = "history"
    MAX_DOWNLOAD_STATUSES = 10

    def __init__(self):
        self.all_downloads_statuses = weakref.WeakKeyDictionary()
        self.recent_download_statuses = []

    def add_download(self, download_status):
        self.all_downloads_statuses[download_status] = None
        self.recent_download_statuses.append(download_status)
        while len(self.recent_download_statuses) > self.MAX_DOWNLOAD_STATUSES:
            self.recent_download_statuses.pop(0)

    def list_all_download_statuses(self):
        for ds in self.all_downloads_statuses:
            yield ds
