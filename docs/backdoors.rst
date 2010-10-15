======================
Statement on Backdoors
======================

October 5, 2010

The New York Times has recently reported that the current U.S. administration
is proposing a bill that would apparently, if passed, require communication
systems to facilitate government wiretapping and access to encrypted data:

 http://www.nytimes.com/2010/09/27/us/27wiretap.html (login required; username/password pairs available at  http://www.bugmenot.com/view/nytimes.com).

Commentary by the  Electronic Frontier Foundation
(https://www.eff.org/deeplinks/2010/09/government-seeks ),  Peter Suderman /
Reason (http://reason.com/blog/2010/09/27/obama-administration-frustrate ),
Julian Sanchez / Cato Institute
(http://www.cato-at-liberty.org/designing-an-insecure-internet/ ).

The core Tahoe developers promise never to change Tahoe-LAFS to facilitate
government access to data stored or transmitted by it. Even if it were
desirable to facilitate such access—which it is not—we believe it would not be
technically feasible to do so without severely compromising Tahoe-LAFS'
security against other attackers. There have been many examples in which
backdoors intended for use by government have introduced vulnerabilities
exploitable by other parties (a notable example being the Greek cellphone
eavesdropping scandal in 2004/5). RFCs  1984 and  2804 elaborate on the
security case against such backdoors.

Note that since Tahoe-LAFS is open-source software, forks by people other than
the current core developers are possible. In that event, we would try to
persuade any such forks to adopt a similar policy.

The following Tahoe-LAFS developers agree with this statement:

David-Sarah Hopwood

Zooko Wilcox-O'Hearn

Brian Warner

Kevan Carstensen

Frédéric Marti

Jack Lloyd

François Deppierraz

Yu Xue

Marc Tooley
