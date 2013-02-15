================================
Storing Shares on Cloud Services
================================

The Tahoe-LAFS storage server can be configured to store its shares on a
cloud storage service, rather than on the local filesystem.


Amazon Simple Storage Service (S3)
==================================

S3 is a commercial storage service provided by Amazon, described at
`<https://aws.amazon.com/s3/>`__.

To enable storing shares on S3, add the following keys to the server's
``tahoe.cfg`` file:

``[storage]``

``backend = cloud.s3``

    This turns off the local filesystem backend and enables use of the cloud
    backend with S3.

``s3.access_key_id = (string, required)``

    This identifies your Amazon Web Services access key. The access key id is
    not secret, but there is a secret key associated with it. The secret key
    is stored in a separate file named ``private/s3secret``.

``s3.bucket = (string, required)``

    This controls which bucket will be used to hold shares. The Tahoe-LAFS
    storage server will only modify and access objects in the configured S3
    bucket. Multiple storage servers cannot share the same bucket.

``s3.url = (URL string, optional)``

    This URL tells the storage server how to access the S3 service. It
    defaults to ``http://s3.amazonaws.com``, but by setting it to something
    else, you may be able to use some other S3-like service if it is
    sufficiently compatible.

The system time of the storage server must be correct to within 15 minutes
in order for S3 to accept the authentication provided with requests.


DevPay
------

Optionally, Amazon `DevPay`_ may be used to delegate billing for a service
based on Tahoe-LAFS and S3 to Amazon Payments.

If DevPay is to be used, the user token and product token (in base64 form)
must be stored in the files ``private/s3usertoken`` and ``private/s3producttoken``
respectively. DevPay-related request headers will be sent only if these files
are present when the server is started. It is currently assumed that only one
user and product token pair is needed by a given storage server.

.. _DevPay: http://docs.amazonwebservices.com/AmazonDevPay/latest/DevPayGettingStartedGuide/


OpenStack and Rackspace Cloud Files
===================================

`OpenStack`_ is an open standard for cloud services, including cloud storage.
Rackspace ( `<https://www.rackspace.com>`__ and `<https://www.rackspace.co.uk>`__ )
provides a storage service called `Cloud Files`_ based on OpenStack.

.. _OpenStack: https://www.openstack.org/
.. _Cloud Files: http://www.rackspace.com/cloud/files/

The authentication service is less precisely specified than other parts of
the OpenStack standards, and so our implementation of it is currently specific
to Rackspace. Other OpenStack storage providers may be supported in future.

To enable storing shares on Rackspace Cloud Files, add the following keys
to the server's ``tahoe.cfg`` file:

``[storage]``

``backend = cloud.openstack``

    This turns off the local filesystem backend and enables use of the cloud
    backend with OpenStack.

``openstack.provider = (string, optional)``

    The supported providers are ``rackspace.com`` and ``rackspace.co.uk``.
    Use the one corresponding to the site on which the Rackspace user account
    was created. The default is ``rackspace.com``.

``openstack.username = (string, required)``

    This identifies the Rackspace user account.

    An API key for the account is also needed. It can be generated
    by logging in at `<https://manage.rackspacecloud.com/APIAccess.do>`__.
    The API key should be stored in a separate file named
    ``private/openstack_api_key``.

``openstack.container = (string, required)``

    This controls which container will be used to hold shares. The Tahoe-LAFS
    storage server will only modify and access objects in the configured
    container. Multiple storage servers cannot share the same container.

``openstack.url = (URL string, optional)``

    This overrides the URL used to access the authentication service. It
    does not need to be set when using a Rackspace account, because the
    correct service is chosen based on ``openstack.provider`` by default.
