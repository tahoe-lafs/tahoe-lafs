==============================
 Expenses paid by donated BTC
==============================

`docs/donations.rst` describes the "Transparent Accounting" that we use for
BTC that has been donated to the Tahoe project. That document lists the
budget items for which we intend to spend these funds, and a Bitcoin public
key for each one. It is signed by the Tahoe-LAFS Release Signing Key, and
gets re-signed each time a new budget item is added.

For every expense that get paid, the BTC will first be moved from the primary
donation key into the budget-item -specific subkey, then moved from that
subkey to whatever vendor or individual is being paid.

This document tracks the actual payments made to each vendor. This file
changes more frequently than `donations.rst`, hence it is *not* signed.
However this file should never reference a budget item or public key which is
not present in `donations.rst`. And every payment in this file should
correspond to a transaction visible on the Bitcoin block chain explorer:

 https://blockchain.info/address/1PxiFvW1jyLM5T6Q1YhpkCLxUh3Fw8saF3

Budget Items
============

Initial Testing
---------------

This was a small transfer to obtain proof-of-spendability for the new wallet.

* Budget: trivial
* Recipient: warner
* Address: 1387fFG7Jg1iwCzfmQ34FwUva7RnC6ZHYG

Expenses/Transactions:

* 17-Mar-2016: deposit+withdrawal of 0.01 BTC
* bcad5f46ebf9fd5d2d7a6a9bed81acf6382cd7216ceddbb5b5f5d968718ec139 (in)
* 13c7f4abf9d6e7f2223c20fefdc47837779bebf3bd95dbb1f225f0d2a2d62c44 (out 1/2)
* 7ca0828ea11fa2f93ab6b8afd55ebdca1415c82c567119d9bd943adbefccce84 (out 2/2)

DNS Registration
----------------

Yearly registration of the `tahoe-lafs.org` domain name.

* Budget: ~$15/yr
* Recipient: warner
* Address: 1552pt6wpudVCRcJaU14T7tAk8grpUza4D

Expenses/Transactions:

* 21-Aug-2012: 1 year, GANDI: $12.50
* 20-Aug-2013: 4 years, GANDI: $64.20
* 4ee7fbcb07f758d51187b6856eaf9999f14a7f3d816fe3afb7393f110814ae5e
  0.11754609 BTC (@$653.41) = $76.70, plus 0.000113 tx-fee



TLS certificates
----------------

Yearly payment for TLS certificates from various vendors. We plan to move to
Lets Encrypt, so 2015 should be last time we pay for a cert.

* Budget: $0-$50/yr
* Recipient: warner
* Address: 1EkT8yLvQhnjnLpJ6bNFCfAHJrM9yDjsqa

Expenses/Transactions:

* 29-Oct-2012: RapidSSL: $49
* 02-Nov-2013: GlobalSign, free for open source projects: $0
* 14-Nov-2014: GANDI: $16
* 28-Oct-2015: GANDI: $16
* e8d1b78fab163baa45de0ec592f8d7547329343181e35c2cdb30e427a442337e
  0.12400489 BTC (@$653.20) = $81, plus 0.000113 tx-fee


Web/Developer Server Hosting
----------------------------

This pays for the rental of a VPS (currently from Linode) for tahoe-lafs.org,
running the project website, Trac, buildbot, and other development tools.

* Budget: $20-$25/month, 2007-present
* Recipient: secorp
* Addresses:
  1MSWNt1R1fohYxgaMV7gJSWbYjkGbXzKWu (<= may-2016)
  1NHgVsq1nAU9x1Bb8Rs5K3SNtzEH95C5kU (>= jun-2016)

Expenses/Transactions:

* Invoice 311312, 12 Feb 2010: $339.83
* Invoice 607395, 05 Jan 2011: $347.39
* Invoice 1183568, 01 Feb 2012: $323.46
* Invoice 1973091, 01 Feb 2013: $323.46
* Invoice 2899489, 01 Feb 2014: $324.00
* Invoice 3387159, 05 July 2014: $6.54 (add backups)
* Multiple invoices monthly 01 Aug 2014 - 01 May 2016: $7.50*22 = $165.00
* Invoice 4083422, 01 Feb 2015: $324.00
* Invoice 5650991, 01 Feb 2016: $324.00
* -- Total through 01 May 2016: $2477.68
* 5861efda59f9ae10952389cf52f968bb469019c77a3642e276a9e35131c36600
  3.78838567 BTC (@$654.02) = $2477.68, plus 0.000113 tx-fee
*
* June 2016 - Oct 2016 $27.45/mo, total $137.25
* 8975b03002166b20782b0f023116b3a391ac5176de1a27e851891bee29c11957
  0.19269107 BTC (@$712.28) = $137.25, plus 0.000113 tx-fee
* (Oops, I forgot the process, and sent the BTC directly secorp's key. I
  should have stuck with the 1MSWN key as the intermediary. Next time I'll go
  back to doing it that way.)


Tahoe Summit
------------

This pays for office space rental and team dinners for each day of the
developer summit.

* Recipient: warner
* Address: 1DskmM8uCvmvTKjPbeDgfmVsGifZCmxouG

* 2016 Summit (Nov 8-9, San Francisco)
* Rental of the Mechanics Institute Library "Board Room": $300/day*2
* Team Dinner (Cha Cha Cha): $164.49
* Team Dinner (Rasoi): $255.34
* -- total: $1019.83
* dcd468fb2792b018e9ebc238e9b93992ad5a8fce48a8ff71db5d79ccbbe30a92
  0.01403961 (@$712.28) = $10, plus 0.000113 tx-fee
* acdfc299c35eed3bb27f7463ad8cdfcdcd4dcfd5184f290f87530c2be999de3e
  1.41401086 (@$714.16) = $1009.83, plus 0.000133 tx-fee

