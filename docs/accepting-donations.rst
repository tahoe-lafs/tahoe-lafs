========================
Storage Server Donations
========================

The following is a configuration convention which allows users to anonymously support the operators of storage servers.
Donations are made using `Zcash shielded transactions`_ to limit the amount of personal information incidentally conveyed.

Sending Donations
=================

To support a storage server following this convention, you need several things:

* a Zcash wallet capable of sending shielded transactions
  (at least until Zcash 1.1.1 this requires a Zcash full node)
* a shielded address with sufficient balance
* a running Tahoe-LAFS client node which knows about the recipient storage server

For additional protection, you may also wish to operate your Zcash wallet and full node using Tor.

Find Zcash Shielded Address
---------------------------

To find an address at which a storage server operator wishes to receive donations,
launch the Tahoe-LAFS web UI::

  $ tahoe webopen

Inspect the page for the storage server area.
This will have a heading like *Connected to N of M known storage servers*.
Each storage server in this section will have a nickname.
A storage server with a nickname beginning with ``zcash:`` is signaling it accepts Zcash donations.
Copy the full address following the ``zcash:`` prefix and save it for the next step.
This is the donation address.
Donation addresses beginning with ``z`` are shielded.
It is recommended that all donations be sent from and to shielded addresses.

Send the Donation
-----------------

First, select a donation amount.
Next, use a Zcash wallet to send the selected amount to the donation address.
Using the Zcash cli wallet, this can be done with commands like::

  $ DONATION_ADDRESS="..."
  $ AMOUNT="..."
  $ YOUR_ADDRESS="..."
  $ zcash-cli z_sendmany $YOUR_ADDRESS "[{\"address\": \"$DONATION_ADDRESS\", \"amount\": $AMOUNT}]"

Remember that you must also have funds to pay the transaction fee
(which defaults to 0.0001 ZEC in mid-2018).

Receiving Donations
===================

To receive donations from users following this convention, you need the following:

* a Zcash shielded address

Configuring Tahoe-LAFS
----------------------

The Zcash shielded address is placed in the storage server's ``nickname`` field.
Edit ``tahoe.cfg`` and edit the ``nickname`` field in the ``node`` section like so::

  [node]
  nickname = zcash:zcABCDEF....

Then restart the storage server.

Further Reading
===============

To acquaint yourself with the security and privacy properties of Zcash,
refer to the `Zcash documentation`_.

.. _Zcash shielded transactions: https://z.cash/support/security/privacy-security-recommendations.html#transaction

.. _Zcash documentation: http://zcash.readthedocs.io/en/latest/
