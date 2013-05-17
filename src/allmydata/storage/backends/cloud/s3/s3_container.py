
from zope.interface import implements

from allmydata.node import InvalidValueError
from allmydata.storage.backends.cloud.cloud_common import IContainer, \
     ContainerRetryMixin, ContainerListMixin


def configure_s3_container(storedir, config):
    accesskeyid = config.get_config("storage", "s3.access_key_id")
    secretkey = config.get_or_create_private_config("s3secret")
    usertoken = config.get_optional_private_config("s3usertoken")
    producttoken = config.get_optional_private_config("s3producttoken")
    if producttoken and not usertoken:
        raise InvalidValueError("If private/s3producttoken is present, private/s3usertoken must also be present.")
    url = config.get_config("storage", "s3.url", "http://s3.amazonaws.com")
    container_name = config.get_config("storage", "s3.bucket")

    return S3Container(accesskeyid, secretkey, url, container_name, usertoken, producttoken)


class S3Container(ContainerRetryMixin, ContainerListMixin):
    implements(IContainer)
    """
    I represent a real S3 container (bucket), accessed using the txaws library.
    """

    def __init__(self, access_key, secret_key, url, container_name, usertoken=None, producttoken=None):
        # We only depend on txaws when this class is actually instantiated.
        from txaws.credentials import AWSCredentials
        from txaws.service import AWSServiceEndpoint
        from txaws.s3.client import S3Client, Query
        from txaws.s3.exception import S3Error

        creds = AWSCredentials(access_key=access_key, secret_key=secret_key)
        endpoint = AWSServiceEndpoint(uri=url)

        query_factory = None
        if usertoken is not None:
            def make_query(*args, **kwargs):
                amz_headers = kwargs.get("amz_headers", {})
                if producttoken is not None:
                    amz_headers["security-token"] = (usertoken, producttoken)
                else:
                    amz_headers["security-token"] = usertoken
                kwargs["amz_headers"] = amz_headers

                return Query(*args, **kwargs)
            query_factory = make_query

        self.client = S3Client(creds=creds, endpoint=endpoint, query_factory=query_factory)
        self.container_name = container_name
        self.ServiceError = S3Error

    def __repr__(self):
        return ("<%s %r>" % (self.__class__.__name__, self.container_name,))

    def create(self):
        return self._do_request('create bucket', self.client.create_bucket, self.container_name)

    def delete(self):
        return self._do_request('delete bucket', self.client.delete_bucket, self.container_name)

    def list_some_objects(self, **kwargs):
        return self._do_request('list objects', self.client.get_bucket, self.container_name, **kwargs)

    def put_object(self, object_name, data, content_type='application/octet-stream', metadata={}):
        return self._do_request('PUT object', self.client.put_object, self.container_name,
                                object_name, data, content_type, metadata)

    def get_object(self, object_name):
        return self._do_request('GET object', self.client.get_object, self.container_name, object_name)

    def head_object(self, object_name):
        return self._do_request('HEAD object', self.client.head_object, self.container_name, object_name)

    def delete_object(self, object_name):
        return self._do_request('DELETE object', self.client.delete_object, self.container_name, object_name)

    def put_policy(self, policy):
        """
        Set access control policy on a bucket.
        """
        query = self.client.query_factory(
            action='PUT', creds=self.client.creds, endpoint=self.client.endpoint,
            bucket=self.container_name, object_name='?policy', data=policy)
        return self._do_request('PUT policy', query.submit)

    def get_policy(self):
        query = self.client.query_factory(
            action='GET', creds=self.client.creds, endpoint=self.client.endpoint,
            bucket=self.container_name, object_name='?policy')
        return self._do_request('GET policy', query.submit)

    def delete_policy(self):
        query = self.client.query_factory(
            action='DELETE', creds=self.client.creds, endpoint=self.client.endpoint,
            bucket=self.container_name, object_name='?policy')
        return self._do_request('DELETE policy', query.submit)
