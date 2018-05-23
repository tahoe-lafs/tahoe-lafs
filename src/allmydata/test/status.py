
class FakeStatus(object):
    def __init__(self):
        self.status = []

    def setServiceParent(self, p):
        pass

    def get_status(self):
        return self.status

    def get_storage_index(self):
        return None

    def get_size(self):
        return None
