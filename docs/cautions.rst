Things To Be Careful About As We Venture Boldly Forth
=====================================================

Timing Attacks
--------------

Asymmetric-key cryptography operations are particularly sensitive to
side-channel attacks. Unless the library is carefully hardened against timing
attacks, it is dangerous to allow an attacker to measure how long signature
and pubkey-derivation operations take. With enough samples, the attacker can
deduce the private signing key from these measurements. (Note that
verification operations are only sensitive if the verifying key is secret,
which is not the case for anything in Tahoe).

We currently use private-key operations in mutable-file writes, and
anticipate using them in signed-introducer announcements and accounting
setup.

Mutable-file writes can reveal timing information to the attacker because the
signature operation takes place in the middle of a read-modify-write cycle.
Modifying a directory requires downloading the old contents of the mutable
file, modifying the contents, signing the new contents, then uploading the
new contents. By observing the elapsed time between the receipt of the last
packet for the download, and the emission of the first packet of the upload,
the attacker will learn information about how long the signature took. The
attacker might ensure that they run one of the servers, and delay responding
to the download request so that their packet is the last one needed by the
client. They might also manage to be the first server to which a new upload
packet is sent. This attack gives the adversary timing information about one
signature operation per mutable-file write. Note that the UCWE
automatic-retry response (used by default in directory modification code) can
cause multiple mutable-file read-modify-write cycles per user-triggered
operation, giving the adversary a slightly higher multiplier.

The signed-introducer announcement involves a signature made as the client
node is booting, before the first connection is established to the
Introducer. This might reveal timing information if any information is
revealed about the client's exact boot time: the signature operation starts a
fixed number of cycles after node startup, and the first packet to the
Introducer is sent a fixed number of cycles after the signature is made. An
adversary who can compare the node boot time against the transmission time of
the first packet will learn information about the signature operation, one
measurement per reboot. We currently do not provide boot-time information in
Introducer messages or other client-to-server data.

In general, we are not worried about these leakages, because timing-channel
attacks typically require thousands or millions of measurements to detect the
(presumably) small timing variations exposed by our asymmetric crypto
operations, which would require thousands of mutable-file writes or thousands
of reboots to be of use to the adversary. However, future authors should take
care to not make changes that could provide additional information to
attackers.
