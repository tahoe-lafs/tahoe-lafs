================================
Storing Shares on Cloud Services
================================

The Tahoe-LAFS storage server can be configured to store its shares on a
cloud storage service, rather than on the local filesystem.

All cloud storage services store the data in a particular container (also
called a "bucket" in some storage services). You can create this container
using the "tahoe admin create-container" command, once you have a correctly
configured Tahoe-LAFS node as described below. That is, configure the node
with the container name you decided to use (e.g. "tahoedata"), then run the
command.

(Currently, "tahoe admin create-container" works only for the S3 and
Azure services. For Rackspace Cloud Files, HP Cloud Object Storage and
Google Cloud Storage, it is necessary to use the respective web interfaces
to create a container for the time being.)


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


OpenStack
=========

`OpenStack`_ is an open standard for cloud services, including cloud storage.
The cloud backend currently supports two OpenStack storage providers:

* Rackspace ( `<https://www.rackspace.com>`__ and `<https://www.rackspace.co.uk>`__ )
  provides a service called `Cloud Files`_.
* HP ( `<https://www.hpcloud.com/>`__ ) provides a service called
  `HP Cloud Object Storage`_.

Other OpenStack storage providers may be supported in future.

.. _OpenStack: https://www.openstack.org/
.. _Cloud Files: http://www.rackspace.com/cloud/files/
.. _HP Cloud Object Storage: https://www.hpcloud.com/products/object-storage

To enable storing shares on one of these services, add the following keys to
the server's ``tahoe.cfg`` file:

``[storage]``

``backend = cloud.openstack``

    This turns off the local filesystem backend and enables use of the cloud
    backend with OpenStack.

``openstack.provider = (string, optional, case-insensitive)``

    The supported providers are ``rackspace.com``, ``rackspace.co.uk``,
    ``hpcloud.com west``, and ``hpcloud.com east``. For Rackspace, use the
    site on which the Rackspace user account was created. For HP, "west"
    and "east" refer to the two storage regions in the United States.

    The default is ``rackspace.com``.

``openstack.container = (string, required)``

    This controls which container will be used to hold shares. The Tahoe-LAFS
    storage server will only modify and access objects in the configured
    container. Multiple storage servers cannot share the same container.

``openstack.url = (URL string, optional)``

    This overrides the URL used to access the authentication service. It
    does not need to be set when using Rackspace or HP accounts, because the
    correct service is chosen based on ``openstack.provider`` by default.

Authentication is less precisely specified than other parts of the OpenStack
standards, and so the two supported providers require slightly different user
credentials, described below.

*If using Rackspace:*

``openstack.username = (string, required)``

    This identifies the Rackspace user account.

    An API key for the account is also needed. It can be generated by
    logging in at `<https://manage.rackspacecloud.com>`__ and selecting
    "Your Account" followed by "API Access" in the left-hand menu, then
    clicking the Show Key button.

    The API key should be stored in a separate file named
    ``private/openstack_api_key``.

*If using HP:*

``openstack.access_key_id = (string, required)``

``openstack.tenant_id = (string, required)``

    These are the Access Key ID and Tenant ID (not the tenant name) obtained
    by logging in at `<https://console.hpcloud.com/account/api_keys>`__.

    The secret key, obtained from the same page by clicking SHOW, should
    be stored in a separate file named ``private/openstack_secret_key``.


Google Cloud Storage
====================

`Google Cloud Storage`_ is a block-based storage system provided by Google. To
access the storage system, you will need to create a project at the `Google
APIs Console`_, and then generate a Service Account client ID in the "API
Access" section. You will store the private key that will be downloaded by
your browser in your Tahoe configuration file; see below.

.. _Google Cloud Storage: https://cloud.google.com/products/cloud-storage
.. _Google APIs Console: https://code.google.com/apis/console/

To enable storing shares on one of these services, add the following keys to
the server's ``tahoe.cfg`` file:

``[storage]``

``backend = cloud.googlestorage``

    This turns off the local filesystem backend and enables use of the cloud
    backend with Google Storage.

``googlestorage.account_email = (string, required)``

    This is the email on the Service Account you created,
    e.g. ``123456@developer.gserviceaccount.com``.

``googlestorage.project_id = (string, required)``

    This is the project number of the project you created,
    e.g. ``123456``. You can find this number in the Google Cloud Storage
    section of the APIs console (the number following `x-goog-project-id`).

``googlestorage.bucket = (string, required)``

    This controls which bucket (a.k.a. container) will be used to hold
    shares. The Tahoe-LAFS storage server will only modify and access objects
    in the configured container. Multiple storage servers cannot share the
    same container. Buckets can be created using a command-line tool (gsutil)
    or a web UI; see the Google Cloud Storage section of the APIs console.

The private key you downloaded is stored in a separate file named
``private/googlestorage_private_key``.


Microsoft Azure Blob Storage
============================

`Microsoft Azure Blob Storage`_ is a block-based storage system provided by
Microsoft. To access the storage system, you will need to `create a storage
account`_. The DNS prefix you choose will be the account name, and either the
resulting primary or secondary keys can be used as the account key; you can
get them by using the "Manage Keys" button at the bottom of the storage
management page.

.. _Microsoft Azure Blob Storage: http://www.windowsazure.com/en-us/manage/services/storage/
.. _create a storage account: http://www.windowsazure.com/en-us/develop/python/how-to-guides/blob-service/#create-account

To enable storing shares in this services, add the following keys to the
server's ``tahoe.cfg`` file:

``[storage]``

``backend = cloud.msazure``

    This turns off the local filesystem backend and enables use of the cloud
    backend with Microsoft Azure.

``msazure.account_name = (string, required)``

    This is the account name (subdomain) you chose when creating the account,
    e.g. ``mydomain``.

``msazure.container = (string, required)``

    This controls which container will be used to hold shares. The Tahoe-LAFS
    storage server will only modify and access objects in the configured
    container. Multiple storage servers cannot share the same container.

The private key you downloaded is stored in a separate file named
``private/msazure_account_key``.
