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


Aspiration Contract
-------------------

In December 2018, we entered into an agreement with a non-profit named
Aspiration (https://aspirationtech.org/) to fund contractors for development
work. They handle payroll, taxes, and oversight, in exchange for an 8%
management fee. The first phase of work will extend through most of 2019.

* Recipient: Aspiration
* Address: 1gDXYQNH4kCJ8Dk7kgiztfjNUaA1KJcHv

These txids record the transfers from the primary 1Pxi address to the
Aspiration-specific 1gDXY subaddress. In some cases, leftover funds
were swept back into the main 1Pxi address after the transfers were
complete.

First phase, transfers performed 28-Dec-2018 - 31-Dec-2018, total 89
BTC, about $350K.

* 95c68d488bd92e8c164195370aaa516dff05aa4d8c543d3fb8cfafae2b811e7a
  1.0 BTC plus 0.00002705 tx-fee
* c0a5b8e3a63c56c4365d4c3ded0821bc1170f6351502849168bc34e30a0582d7
  89.0 BTC plus 0.00000633 tx-fee
* 421cff5f398509aaf48951520738e0e63dfddf1157920c15bdc72c34e24cf1cf
  return 0.00005245 BTC to 1Pxi, less 0.00000211 tx-fee

In November 2020, we funded a second phase of the work: 51.38094 BTC,
about $800K.

* 7558cbf3b24e8d835809d2d6f01a8ba229190102efdf36280d0639abaa488721
  1.0 BTC plus 0.00230766 tx-fee
* 9c78ae6bb7db62cbd6be82fd52d50a2f015285b562f05de0ebfb0e5afc6fd285
  56.0 BTC plus 0.00057400 tx-fee
* fbee4332e8c7ffbc9c1bcaee773f063550e589e58d350d14f6daaa473966c368
  returning 5.61906 BTC to 1Pxi, less 0.00012000 tx-fee


Open Collective
---------------

In August 2023, we started working with Open Collective to fund a
grant covering development work performed over the last year, and
onwards into 2024.

* Recipient: Open Collective (US)

  * Transfer Address: 1KZYr8UU2XjuEdSPzn2pF8eRPZZvffByDf
  * Payment Address: 3LVNG26VxfE6RXJJjKCVNHnAGMtyDrx9WU

The first phase transferred 7.5 BTC (about $250K).

* 25-26-oct-2023: ~7.5 BTC ($250k)
  * transfer address: 1KZYr8UU2XjuEdSPzn2pF8eRPZZvffByDf
  * xfer 0.1 BTC: txid 9bfe10e3f240724d0d15bcd6405f4e568b5f1fb1dc2069d0ecf20df22d6ee502
  * xfer 7.39994304 BTC: txid 882dca0e1acc2e203b2ecfbb20d70dc2018840bed7f4ad4e1b8c629d2b3f1136
  * payment address: 3LVNG26VxfE6RXJJjKCVNHnAGMtyDrx9WU
  * send 0.01 BTC txid 24ca9a87e8022802ccae2db1310636973d2caf0e3f46892490cb896d03f2e795
  * send 7.48969257 BTC txid a83ff318a1d56b8f95c10d1740fbd1fd1065958d4e1c83ef39a8ec9e50f08ddf
* 06-jan-2024: 5.0 BTC ($224k)
  * transfer address: 1KZYr8UU2XjuEdSPzn2pF8eRPZZvffByDf
  * xfer 5.0 BTC: txid 6f0af3fe6eeaf51d9054a7f666c90898aaa7b203deb2cbf89164fca0517953c0
  * payment address: 3LVNG26VxfE6RXJJjKCVNHnAGMtyDrx9WU
  * send 0.01 BTC: txid 20e4afdf6eec1dad8968164eed187de1e840a5064c09f03bbded48fee24deb71
  * send 4.989379 BTC: txid 0f210c9ae279d912482cc3cbcd40df53fd4fe7644ba8d25fbb3e42de5140ad15
* 25-apr-2024: 5.0 BTC ($316k) (current price: $64,385.24)
  * transfer address: 1KZYr8UU2XjuEdSPzn2pF8eRPZZvffByDf
  * xfer 5.0 BTC: txid 01a4c2cb18b95025ac8074aa1ccd46ec1f3783d5d9b15ef5bb0d57a59fe09e5b , block 840,915
  * payment address: 3LVNG26VxfE6RXJJjKCVNHnAGMtyDrx9WU
  * send 4.9999487 BTC: txid a840f2c14a9acc2d2d3ecd35dff69a0d1904151825262a52dd397a22487e9ec8
* 21-aug-2024: 6.0 BTC ($350k) (current price: $59,308)
  * transfer address: 1KZYr8UU2XjuEdSPzn2pF8eRPZZvffByDf
  * xfer 6.0 BTC: txid 766fa17b43ab0d2a0c3d2839a59e9887abf3026d44ccecee42a348fb2cc05474, block 857,777
  * payment address: 3LVNG26VxfE6RXJJjKCVNHnAGMtyDrx9WU
  * send 5.999943 BTC: txid b44c8d4dbbfcef6eee2681700ccb8e3c6d7d56b3796ce8813848d2c91022d7a4, block 857,788
* 10-dec-2024: 2.0 BTC ($200k) (current price: $97,745)
  * transfer address: 1KZYr8UU2XjuEdSPzn2pF8eRPZZvffByDf
  * xfer 2.0 BTC: txid 134afb660302a8f1940af32ea55b92719477dd77615d6457ffeabf99dd085773, block 874,225
  * payment address: 3LVNG26VxfE6RXJJjKCVNHnAGMtyDrx9WU
  * send 1.999943 BTC: txid 05bbeb58a7248bd542c9dab1562c561a98ece72bb44f65be82f50c7fc2c47b92, block 874,226
* 21-jan-2025: 1.0 BTC ($106k) (current price: $105,923)
  * transfer address: 1KZYr8UU2XjuEdSPzn2pF8eRPZZvffByDf
  * xfer 1.0 BTC: txid c4b90f29136a8df546ce30bfa238dbb479020b85c26eb33b1f221382cec8c235, block 880,263
  * payment address: 3LVNG26VxfE6RXJJjKCVNHnAGMtyDrx9WU
  * send 0.999943 BTC: txid b480d111123b3de09ce8d757954a2ff47e4e8efce53b6ba81823a7ae9a10e8ff, block 880,270
* 04-feb-2025: 3.0 BTC ($295k) (current price: $98,340)
  * transfer address: 1KZYr8UU2XjuEdSPzn2pF8eRPZZvffByDf
  * xfer 3.0 BTC: txid 478d682cf9b65e1ee5d0a6bcabf3d21d3615340288b1611a4f53e49d81d80394, block 882,334
  * payment address: 3LVNG26VxfE6RXJJjKCVNHnAGMtyDrx9WU
  * send 2.999943 BTC: txid 5734b5e06cd1d6576344be05d9087b2838f38330afd508a2c5866dc23f02bc67, block 882,335


Magic Internet Project
----------------------

In January 2025, we started supporting the Magic Internet project with
a two-year commitment of 20.0 BTC. Initially, the funds will be
transferred in three-month chunks through Open Collective. In late
2025, to guard against BTC price fluctuations, the remainder of the
funds were transferred in a lump sum.

* Recipient: Magic Internet Project (via Open Collective)

  * Transfer Address: 12HZ2rGfgcAqJY2Y55Dxt4Z2qUXiK2WFf8
  * Payment Address: 3LVNG26VxfE6RXJJjKCVNHnAGMtyDrx9WU (same as above)

* 18-jan-2025: ~3.0 BTC ($300k)
  * transfer address: 12HZ2rGfgcAqJY2Y55Dxt4Z2qUXiK2WFf8
  * xfer 0.1 BTC: txid d6e9688dfc75289343729c528914099671a1b82a80dfb6260b9791085e1445f3, block 880,266
  * xfer 2.9 BTC: txid df060a796a449b67a78d3ac634f2b601f8b7c0b9b68e58cdfba3dd333d16fc3a, block 880,271
  * payment address: 3LVNG26VxfE6RXJJjKCVNHnAGMtyDrx9WU
  * send 2.9998986 BTC: 374e7ec1a9200b83d65976dbeedb164d6be7d4669b630436276654a096821b56, block 880,272

* 06-mar-2025: ~3.0 BTC ($264k)
  * transfer: 12HZ2rGfgcAqJY2Y55Dxt4Z2qUXiK2WFf8
  * xfer 3.0 BTC: txid 2284e33e5fa63312b05b31b65a301a73d16f455ab463bb02bd78043a3098146c, block 886,694
  * payment address: 3LVNG26VxfE6RXJJjKCVNHnAGMtyDrx9WU
  * send 2.999943 BTC: txid f5a85e6b9a761b64ff17e85dfaa2f51f7729f4534a48674883f7fd49384adc5c, block 886,695

* 27-jun-2025: ~3.0 BTC ($322k)
  * transfer: 12HZ2rGfgcAqJY2Y55Dxt4Z2qUXiK2WFf8
  * xfer 3.0 BTC: txid ee6f505592a5ccefbe5bbc144822fbd2c4e6f9a5b726b036e9e318bfab98cf5e, block 902,989
  * payment address: 3LVNG26VxfE6RXJJjKCVNHnAGMtyDrx9WU
  * send 2.999943 BTC: txid f4e6df8d536f06a15ffdec7d623b15b4680b5321c6fc07139bc8b3bffc8dd681, block 902,993

* 05-sep-2025: ~11.0 BTC ($1217k)
  * transfer: 12HZ2rGfgcAqJY2Y55Dxt4Z2qUXiK2WFf8
  * xfer 11.0 BTC: txid 9d272e6db6ad7be2aef5f25c3f08e7e5cad6f6d0176a1d5808f96606ed9f5101, block 913,316
  * payment address: 3LVNG26VxfE6RXJJjKCVNHnAGMtyDrx9WU
  * send 10.999943 BTC: txid db1cfe74265c9117d6580c40d4edb4a85cd297aa5a2fafe749b3867e41a07017, block 913,317
