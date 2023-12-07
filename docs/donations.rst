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
  $0-$50/yr, ending 2015 (when we switched to LetsEncrypt)
  1EkT8yLvQhnjnLpJ6bNFCfAHJrM9yDjsqa

* website/dev-server hosting (on Linode, paid by secorp)
  ~$20-$25/mo, 2007-present
  1MSWNt1R1fohYxgaMV7gJSWbYjkGbXzKWu (<= may-2016)
  1NHgVsq1nAU9x1Bb8Rs5K3SNtzEH95C5kU (>= jun-2016)

* 2016 Tahoe Summit expenses: venue rental, team dinners (paid by warner)
  ~$1020
  1DskmM8uCvmvTKjPbeDgfmVsGifZCmxouG

* Aspiration contract
  $300k-$350k (first phase, 2019)
  $800k (second phase, 2020)
  1gDXYQNH4kCJ8Dk7kgiztfjNUaA1KJcHv

* OpenCollective development work (2023)
  ~$260k
  1KZYr8UU2XjuEdSPzn2pF8eRPZZvffByDf


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

signed: Brian Warner, 25-Oct-2023


-----BEGIN PGP SIGNATURE-----

iQEzBAEBCAAdFiEE405i0G0Oac/KQXn/veDTHWhmanoFAmU5YZMACgkQveDTHWhm
anqt+ggAo2kulNmjrWA5VhqE8i6ckkxQMRVY4y0LAfiI0ho/505ZBZvpoh/Ze31x
ZJj4DczHmZM+m3L+fZyubT4ldagYEojtwkYmxHAQz2DIV4PrdjsUQWyvkNcTBZWu
y5mR5ATk3EYRa19xGEosWK1OzW2kgRbpAbznuWsdxxw9vNENBrolGRsyJqRQHCiV
/4UkrGiOegaJSFMKy2dCyDF3ExD6wT9+fdqC5xDJZjhD+SUDJnD4oWLYLroj//v1
sy4J+/ElNU9oaC0jDb9fx1ECk+u6B+YiaYlW/MrZNqzKCM/76yZ8sA2+ynsOHGtL
bPFpLJjX6gBwHkMqvkWhsJEojxkFVQ==
=gxlb
-----END PGP SIGNATURE-----
