-----BEGIN PGP SIGNED MESSAGE-----
Hash: SHA256

=========
Donations
=========

Donations to the Tahoe-LAFS project are welcome, and can be made to the
following Bitcoin address:

 1PxiFvW1jyLM5T6Q1YhpkCLxUh3Fw8saF3

The funds currently available to the project are visible through the
blockchain explorer:

 https://blockchain.info/address/1PxiFvW1jyLM5T6Q1YhpkCLxUh3Fw8saF3

Governance
==========

The Tahoe-LAFS Software Foundation manages these funds. Our intention is
to use them for operational expenses (website hosting, test
infrastructure, EC2 instance rental, and SSL certificates). Future uses
might include developer summit expenses, bug bounties, contract services
(e.g. graphic design for the web site, professional security review of
codebases, development of features outside the core competencies of the
main developers), and student sponsorships.

The Foundation currently consists of secorp (Peter Secor), warner (Brian
Warner), and zooko (Zooko Wilcox).

Transparent Accounting
======================

Our current plan is to leave all funds in the main `1Pxi` key until they
are spent. For each declared budget item, we will allocate a new public
key, and transfer funds to that specific key before distributing them to
the ultimate recipient. All expenditures can thus be tracked on the
blockchain.

Some day, we might choose to move the funds into a more sophisticated
type of key (e.g. a 2-of-3 multisig address). If/when that happens, we
will publish the new donation address, and transfer all funds to it. We
will continue the plan of keeping all funds in the (new) primary
donation address until they are spent.

Expenditure Addresses
=====================

* Initial testing / proof-of-spendability (warner)
  1387fFG7Jg1iwCzfmQ34FwUva7RnC6ZHYG
  17-Mar-2016: deposit+withdrawal of 0.01 BTC
* website/dev-server hosting (on Linode, paid by secorp)
  address TBD
  ~$20/mo, 2007-present
* tahoe-lafs.org DNS registration and SSL certificates (paid by warner)
  address TBD
  21-Aug-2012: DNS (1 year, GANDI) $12.50
  29-Oct-2012: SSL (RapidSSL) $49
  20-Aug-2013: DNS (4 years, GANDI) $64.20
  02-Nov-2013: SSL (GlobalSign, free for open source projects) $0
  14-Nov-2014: SSL (GANDI) $16
  28-Oct-2015: SSL (GANDI) $16


Historical Donation Addresses
=============================

The Tahoe project has had a couple of different donation addresses over
the years, managed by different people. All of these funds have been (or
will be) transferred to the current primary donation address (`1Pxi`).

* 13GrdS9aLXoEbcptBLQi7ffTsVsPR7ubWE (21-Aug-2010 - 23-Aug-2010)
  Managed by secorp, total receipts: 17 BTC
* 19jzBxijUeLvcMVpUYXcRr5kGG3ThWgx4P (23-Aug-2010 - 29-Jan-2013)
  Managed by secorp, total receipts: 358.520276 BTC
* 14WTbezUqWSD3gLhmXjHD66jVg7CwqkgMc (24-May-2013 - 21-Mar-2016)
  Managed by luckyredhot, total receipts: 3.97784278 BTC
  stored in 19jXek4HRL54JrEwNPyEzitPnkew8XPkd8
* 1PxiFvW1jyLM5T6Q1YhpkCLxUh3Fw8saF3 (21-Mar-2016 - present)
  Managed by warner, backups with others

Validation
==========

This document is signed by the Tahoe-LAFS Release-Signing Key (GPG keyid
2048R/68666A7A, fingerprint E34E 62D0 6D0E 69CF CA41 79FF BDE0 D31D 6866
6A7A). It is also committed to the Tahoe source tree
(https://github.com/tahoe-lafs/tahoe-lafs.git) as `docs/donations.rst`.
Both actions require access to secrets held closely by Tahoe developers.

signed: Brian Warner, 21-Mar-2016


-----BEGIN PGP SIGNATURE-----
Version: GnuPG v1

iQEcBAEBCAAGBQJW76qNAAoJEL3g0x1oZmp6rXoIAIG6g3BdFNKjseWDbdKX90Mf
465M9WaqPAccNvGn/l/ob1AhWfgB5lrZa0asajh5noZ00UjRnUuEbDMcXGKDXy6f
0wg+JQdSLhTLEYlYEqqHnToiJwboY/WXnxtgaH19wfwdfuyUBSIKbFofYdX638+0
qgpho35lWGuD17mCGKVdJy6N9U4W8uY/9eIoyrAId+4TLs4SJCRA4+vJlyvntsvb
+VJ74p1IGjrkudoJhiqqjSgxpcbAsyntWbssmBj3x7YdC6AcJRZQcrz2R4O1kyF7
n62uDmtySXOam4ZQqKron5I4gJ+iPqggeBnn5Kt7LwB3e/gYxnSUInGlbSkv2Ao=
=Axmj
-----END PGP SIGNATURE-----
