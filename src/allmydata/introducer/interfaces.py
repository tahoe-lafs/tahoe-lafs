# -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-
# -*- indent-tabs-mode: nil -*-

from zope.interface import Interface
from foolscap.api import StringConstraint, TupleOf, SetOf, DictOf, Any, \
    RemoteInterface, Referenceable
from old import RIIntroducerSubscriberClient_v1
FURL = StringConstraint(1000)

# old introducer protocol (v1):
#
# Announcements are (FURL, service_name, remoteinterface_name,
#                    nickname, my_version, oldest_supported)
#  the (FURL, service_name, remoteinterface_name) refer to the service being
#  announced. The (nickname, my_version, oldest_supported) refer to the
#  client as a whole. The my_version/oldest_supported strings can be parsed
#  by an allmydata.util.version.Version instance, and then compared. The
#  first goal is to make sure that nodes are not confused by speaking to an
#  incompatible peer. The second goal is to enable the development of
#  backwards-compatibility code.

Announcement_v1 = TupleOf(FURL, str, str,
                          str, str, str)

# v2 protocol over foolscap: Announcements are 3-tuples of (bytes, str, str)
# or (bytes, none, none)
Announcement_v2 = Any()

class RIIntroducerSubscriberClient_v2(RemoteInterface):
    __remote_name__ = "RIIntroducerSubscriberClient_v2.tahoe.allmydata.com"

    def announce_v2(announcements=SetOf(Announcement_v2)):
        """I accept announcements from the publisher."""
        return None

    def set_encoding_parameters(parameters=(int, int, int)):
        """Advise the client of the recommended k-of-n encoding parameters
        for this grid. 'parameters' is a tuple of (k, desired, n), where 'n'
        is the total number of shares that will be created for any given
        file, while 'k' is the number of shares that must be retrieved to
        recover that file, and 'desired' is the minimum number of shares that
        must be placed before the uploader will consider its job a success.
        n/k is the expansion ratio, while k determines the robustness.

        Introducers should specify 'n' according to the expected size of the
        grid (there is no point to producing more shares than there are
        peers), and k according to the desired reliability-vs-overhead goals.

        Note that setting k=1 is equivalent to simple replication.
        """
        return None

SubscriberInfo = DictOf(str, Any())

class RIIntroducerPublisherAndSubscriberService_v2(RemoteInterface):
    """To publish a service to the world, connect to me and give me your
    announcement message. I will deliver a copy to all connected subscribers.
    To hear about services, connect to me and subscribe to a specific
    service_name."""
    __remote_name__ = "RIIntroducerPublisherAndSubscriberService_v2.tahoe.allmydata.com"
    def get_version():
        return DictOf(str, Any())
    def publish(announcement=Announcement_v1):
        return None
    def publish_v2(announcement=Announcement_v2, canary=Referenceable):
        return None
    def subscribe(subscriber=RIIntroducerSubscriberClient_v1, service_name=str):
        return None
    def subscribe_v2(subscriber=RIIntroducerSubscriberClient_v2,
                     service_name=str, subscriber_info=SubscriberInfo):
        """Give me a subscriber reference, and I will call its announce_v2()
        method with any announcements that match the desired service name. I
        will ignore duplicate subscriptions. The subscriber_info dictionary
        tells me about the subscriber, and is used for diagnostic/status
        displays."""
        return None

class IIntroducerClient(Interface):
    """I provide service introduction facilities for a node. I help nodes
    publish their services to the rest of the world, and I help them learn
    about services available on other nodes."""

    def publish(service_name, ann, signing_key=None):
        """Publish the given announcement dictionary (which must be
        JSON-serializable), plus some additional keys, to the world.

        Each announcement is characterized by a (service_name, serverid)
        pair. When the server sees two announcements with the same pair, the
        later one will replace the earlier one. The serverid is derived from
        the signing_key, if present, otherwise it is derived from the
        'anonymous-storage-FURL' key.

        If signing_key= is set to an instance of SigningKey, it will be
        used to sign the announcement."""

    def subscribe_to(service_name, callback, *args, **kwargs):
        """Call this if you will eventually want to use services with the
        given SERVICE_NAME. This will prompt me to subscribe to announcements
        of those services. Your callback will be invoked with at least two
        arguments: a pubkey and an announcement dictionary, followed by any
        additional callback args/kwargs you gave me. The pubkey will be None
        unless the announcement was signed by the corresponding pubkey, in
        which case it will be a printable string like 'v0-base32..'.

        I will run your callback for both new announcements and for
        announcements that have changed, but you must be prepared to tolerate
        duplicates.

        The announcement that I give you comes from some other client. It
        will be a JSON-serializable dictionary which (by convention) is
        expected to have at least the following keys:

         version: 0
         nickname: unicode
         app-versions: {}
         my-version: str
         oldest-supported: str

         service-name: str('storage')
         anonymous-storage-FURL: str(furl)

        Note that app-version will be an empty dictionary if either the
        publishing client or the Introducer are running older code.
        """

    def connected_to_introducer():
        """Returns a boolean, True if we are currently connected to the
        introducer, False if not."""

