import os

# 'app' is overwritten by manhole when the connection is established. We set
# it to None now to keep pyflakes from complaining.
app = None

def get_random_bucket_on(nodeid, size=200):
    d = app.get_remote_service(nodeid, 'storageserver')
    def get_bucket(rss):
        return rss.callRemote('allocate_bucket',
                              verifierid=os.urandom(20),
                              bucket_num=26,
                              size=size,
                              leaser=app.tub.tubID,
                              )
    d.addCallback(get_bucket)
    return d

def write_to_bucket(bucket, bytes=100):
    return bucket.callRemote('write', data=os.urandom(bytes))

