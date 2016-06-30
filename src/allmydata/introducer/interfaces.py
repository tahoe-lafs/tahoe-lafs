
from zope.interface import Interface
from foolscap.api import StringConstraint, SetOf, DictOf, Any, \
    RemoteInterface, Referenceable
FURL = StringConstraint(1000)

# v2 protocol over foolscap: Announcements are 3-tuples of (msg, sig_vs,
# claimed_key_vs):
# * msg (bytes): UTF-8(json(ann_dict))
#   * ann_dict has IntroducerClient-provided keys like "version", "nickname",
#     "app-versions", "my-version", "oldest-supported", and "service-name".
#     Plus service-specific keys like "anonymous-storage-FURL" and
#     "permutation-seed-base32" (both for service="storage").
# * sig_vs (str): "v0-"+base32(signature(msg))
# * claimed_key_vs (str): "v0-"+base32(pubkey)

# (nickname, my_version, oldest_supported) refer to the client as a whole.
# The my_version/oldest_supported strings can be parsed by an
# allmydata.util.version.Version instance, and then compared. The first goal
# is to make sure that nodes are not confused by speaking to an incompatible
# peer. The second goal is to enable the development of
# backwards-compatibility code.

# Note that old v1 clients (which are gone now) did not sign messages, so v2
# servers would deliver v2-format messages with sig_vs=claimed_key_vs=None.
# These days we should always get a signature and a pubkey.

Announcement_v2 = Any()

class RIIntroducerSubscriberClient_v2(RemoteInterface):
    __remote_name__ = "RIIntroducerSubscriberClient_v2.tahoe.allmydata.com"

    def announce_v2(announcements=SetOf(Announcement_v2)):
        """I accept announcements from the publisher."""
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
    def publish_v2(announcement=Announcement_v2, canary=Referenceable):
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

