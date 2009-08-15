
import weakref

class History:
    """Keep track of recent operations, for a status display."""

    name = "history"
    MAX_DOWNLOAD_STATUSES = 10
    MAX_UPLOAD_STATUSES = 10
    MAX_MAPUPDATE_STATUSES = 20
    MAX_PUBLISH_STATUSES = 20
    MAX_RETRIEVE_STATUSES = 20

    def __init__(self, stats_provider=None):
        self.stats_provider = stats_provider

        self.all_downloads_statuses = weakref.WeakKeyDictionary()
        self.recent_download_statuses = []
        self.all_upload_statuses = weakref.WeakKeyDictionary()
        self.recent_upload_statuses = []

        self.all_mapupdate_status = weakref.WeakKeyDictionary()
        self.recent_mapupdate_status = []
        self.all_publish_status = weakref.WeakKeyDictionary()
        self.recent_publish_status = []
        self.all_retrieve_status = weakref.WeakKeyDictionary()
        self.recent_retrieve_status = []

        self.all_helper_upload_statuses = weakref.WeakKeyDictionary()
        self.recent_helper_upload_statuses = []


    def add_download(self, download_status):
        self.all_downloads_statuses[download_status] = None
        self.recent_download_statuses.append(download_status)
        while len(self.recent_download_statuses) > self.MAX_DOWNLOAD_STATUSES:
            self.recent_download_statuses.pop(0)

    def list_all_download_statuses(self):
        for ds in self.all_downloads_statuses:
            yield ds

    def add_upload(self, upload_status):
        self.all_upload_statuses[upload_status] = None
        self.recent_upload_statuses.append(upload_status)
        while len(self.recent_upload_statuses) > self.MAX_UPLOAD_STATUSES:
            self.recent_upload_statuses.pop(0)

    def list_all_upload_statuses(self):
        for us in self.all_upload_statuses:
            yield us



    def notify_mapupdate(self, p):
        self.all_mapupdate_status[p] = None
        self.recent_mapupdate_status.append(p)
        while len(self.recent_mapupdate_status) > self.MAX_MAPUPDATE_STATUSES:
            self.recent_mapupdate_status.pop(0)

    def notify_publish(self, p, size):
        self.all_publish_status[p] = None
        self.recent_publish_status.append(p)
        if self.stats_provider:
            self.stats_provider.count('mutable.files_published', 1)
            # We must be told bytes_published as an argument, since the
            # publish_status does not yet know how much data it will be asked
            # to send. When we move to MDMF we'll need to find a better way
            # to handle this.
            self.stats_provider.count('mutable.bytes_published', size)
        while len(self.recent_publish_status) > self.MAX_PUBLISH_STATUSES:
            self.recent_publish_status.pop(0)

    def notify_retrieve(self, r):
        self.all_retrieve_status[r] = None
        self.recent_retrieve_status.append(r)
        if self.stats_provider:
            self.stats_provider.count('mutable.files_retrieved', 1)
            self.stats_provider.count('mutable.bytes_retrieved', r.get_size())
        while len(self.recent_retrieve_status) > self.MAX_RETRIEVE_STATUSES:
            self.recent_retrieve_status.pop(0)


    def list_all_mapupdate_statuses(self):
        for s in self.all_mapupdate_status:
            yield s
    def list_all_publish_statuses(self):
        for s in self.all_publish_status:
            yield s
    def list_all_retrieve_statuses(self):
        for s in self.all_retrieve_status:
            yield s

    def notify_helper_upload(self, s):
        self.all_helper_upload_statuses[s] = None
        self.recent_helper_upload_statuses.append(s)
        while len(self.recent_helper_upload_statuses) > self.MAX_UPLOAD_STATUSES:
            self.recent_helper_upload_statuses.pop(0)

    def list_all_helper_statuses(self):
        for s in self.all_helper_upload_statuses:
            yield s

