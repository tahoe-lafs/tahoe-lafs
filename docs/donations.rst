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

This lists the public key used for each declared budget item. The individual
payments will be recorded in a separate file (see `docs/expenses.rst`), which
is not signed. All transactions from the main `1Pxi` key should be to some
key on this list.

* Initial testing (warner)
  1387fFG7Jg1iwCzfmQ34FwUva7RnC6ZHYG
  one-time 0.01 BTC deposit+withdrawal

* tahoe-lafs.org DNS registration (paid by warner)
  1552pt6wpudVCRcJaU14T7tAk8grpUza4D
  ~$15/yr for DNS

* tahoe-lafs.org SSL certificates (paid by warner)
  $0-$50/yr, probably ending 2015
  1EkT8yLvQhnjnLpJ6bNFCfAHJrM9yDjsqa

* website/dev-server hosting (on Linode, paid by secorp)
  1MSWNt1R1fohYxgaMV7gJSWbYjkGbXzKWu
  ~$20-$25/mo, 2007-present


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

signed: Brian Warner, 08-Jul-2016


-----BEGIN PGP SIGNATURE-----
Version: GnuPG v2

iQEcBAEBCAAGBQJXf/v7AAoJEL3g0x1oZmp6eQ8H/A3orFJiSQm1vyZgQWPneKqb
m7QaPIl5usKcZoGOtOcX4KlhvtOCHUcMjEHe1O+UbSWsKQfQQGiTMF+luwVh5DGZ
Gmn/gSVVPYYa6MGZaoai0lCE1zh4wMuWsTZWwtjCqvN+bnYud6fbrem6p9XSbcpK
bImMmig8/OJ0/Y0qhth6CBEqRm8LiALetibzcyEtWG2kwLTV/QYOqbfY3ZPI++bV
q4VmDP80dNDKDq2JT6ZNU377Biz2/z/oj1ILb11aAylJJ4BV/3L+hJmyLfr0rsMc
lSTzqAM2PUINnR4LnVdtDjJzBNRpcn535VxQFUc8e/jlOJo1rJse0Pt7WkDFsUA=
=fyOh
-----END PGP SIGNATURE-----
