
from zope.interface import Interface
from foolscap.schema import StringConstraint, TupleOf, SetOf, DictOf, Any
from foolscap import RemoteInterface
FURL = StringConstraint(1000)

# Announcements are (FURL, service_name, remoteinterface_name,
#                    nickname, my_version, oldest_supported)
#  the (FURL, service_name, remoteinterface_name) refer to the service being
#  announced. The (nickname, my_version, oldest_supported) refer to the
#  client as a whole. The my_version/oldest_supported strings can be parsed
#  by an allmydata.util.version.Version instance, and then compared. The
#  first goal is to make sure that nodes are not confused by speaking to an
#  incompatible peer. The second goal is to enable the development of
#  backwards-compatibility code.

Announcement = TupleOf(FURL, str, str,
                       str, str, str)

class RIIntroducerSubscriberClient(RemoteInterface):
    __remote_name__ = "RIIntroducerSubscriberClient.tahoe.allmydata.com"

    def announce(announcements=SetOf(Announcement)):
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

# When Foolscap can handle multiple interfaces (Foolscap#17), the
# full-powered introducer will implement both RIIntroducerPublisher and
# RIIntroducerSubscriberService. Until then, we define
# RIIntroducerPublisherAndSubscriberService as a combination of the two, and
# make everybody use that.

class RIIntroducerPublisher(RemoteInterface):
    """To publish a service to the world, connect to me and give me your
    announcement message. I will deliver a copy to all connected subscribers."""
    __remote_name__ = "RIIntroducerPublisher.tahoe.allmydata.com"

    def publish(announcement=Announcement):
        # canary?
        return None

class RIIntroducerSubscriberService(RemoteInterface):
    __remote_name__ = "RIIntroducerSubscriberService.tahoe.allmydata.com"

    def subscribe(subscriber=RIIntroducerSubscriberClient, service_name=str):
        """Give me a subscriber reference, and I will call its new_peers()
        method will any announcements that match the desired service name. I
        will ignore duplicate subscriptions.
        """
        return None

class RIIntroducerPublisherAndSubscriberService(RemoteInterface):
    __remote_name__ = "RIIntroducerPublisherAndSubscriberService.tahoe.allmydata.com"
    def get_version():
        return DictOf(str, Any())
    def publish(announcement=Announcement):
        return None
    def subscribe(subscriber=RIIntroducerSubscriberClient, service_name=str):
        return None

class IIntroducerClient(Interface):
    """I provide service introduction facilities for a node. I help nodes
    publish their services to the rest of the world, and I help them learn
    about services available on other nodes."""

    def publish(furl, service_name, remoteinterface_name):
        """Once you call this, I will tell the world that the Referenceable
        available at FURL is available to provide a service named
        SERVICE_NAME. The precise definition of the service being provided is
        identified by the Foolscap 'remote interface name' in the last
        parameter: this is supposed to be a globally-unique string that
        identifies the RemoteInterface that is implemented."""

    def subscribe_to(service_name):
        """Call this if you will eventually want to use services with the
        given SERVICE_NAME. This will prompt me to subscribe to announcements
        of those services. You can pick up the announcements later by calling
        get_all_connections_for() or get_permuted_peers().
        """

    def get_all_connections():
        """Return a frozenset of (nodeid, service_name, rref) tuples, one for
        each active connection we've established to a remote service. This is
        mostly useful for unit tests that need to wait until a certain number
        of connections have been made."""

    def get_all_connectors():
        """Return a dict that maps from (nodeid, service_name) to a
        RemoteServiceConnector instance for all services that we are actively
        trying to connect to. Each RemoteServiceConnector has the following
        public attributes::

          service_name: the type of service provided, like 'storage'
          announcement_time: when we first heard about this service
          last_connect_time: when we last established a connection
          last_loss_time: when we last lost a connection

          version: the peer's version, from the most recent connection
          oldest_supported: the peer's oldest supported version, same

          rref: the RemoteReference, if connected, otherwise None
          remote_host: the IAddress, if connected, otherwise None

        This method is intended for monitoring interfaces, such as a web page
        which describes connecting and connected peers.
        """

    def get_all_peerids():
        """Return a frozenset of all peerids to whom we have a connection (to
        one or more services) established. Mostly useful for unit tests."""

    def get_all_connections_for(service_name):
        """Return a frozenset of (nodeid, service_name, rref) tuples, one
        for each active connection that provides the given SERVICE_NAME."""

    def get_permuted_peers(service_name, key):
        """Returns an ordered list of (peerid, rref) tuples, selecting from
        the connections that provide SERVICE_NAME, using a hash-based
        permutation keyed by KEY. This randomizes the service list in a
        repeatable way, to distribute load over many peers.
        """

    def connected_to_introducer():
        """Returns a boolean, True if we are currently connected to the
        introducer, False if not."""

