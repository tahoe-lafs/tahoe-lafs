
=================
Release Checklist
=================

These instructions were produced while making the 1.15.0 release. They
are based on the original instructions (in old revisions in the file
`docs/how_to_make_a_tahoe-lafs_release.org`).

Any contributor can do the first part of the release preparation. Only
certain contributors can perform other parts. These are the two main
sections of this checklist (and could be done by different people).

A final section describes how to announce the release.


Any Contributor
---------------

Anyone who can create normal PRs should be able to complete this
portion of the release process.


Prepare for the Release
```````````````````````

The `master` branch should always be releasable.

It may be worth asking (on IRC or mailing-ist) if anything will be
merged imminently (for example, "I will prepare a release this coming
Tuesday if you want to get anything in").

- Create a ticket for the release in Trac
- Ticket number needed in next section


Create Branch and Apply Updates
```````````````````````````````

- Create a branch for release-candidates (e.g. `XXXX.release-1.15.0.rc0`)
- run `tox -e news` to produce a new NEWS.txt file (this does a commit)
- create the news for the release

  - newsfragments/<ticket number>.minor
  - commit it

- manually fix NEWS.txt

  - proper title for latest release ("Release 1.15.0" instead of "Release ...post1432")
  - double-check date (maybe release will be in the future)
  - spot-check the release notes (these come from the newsfragments
    files though so don't do heavy editing)
  - commit these changes

- update "relnotes.txt"

  - update all mentions of 1.14.0 -> 1.15.0
  - update "previous release" statement and date
  - summarize major changes
  - commit it

- update "nix/tahoe-lafs.nix"

  - change the value given for `version` from `OLD.post1` to `NEW.post1`

- update "CREDITS"

  - are there any new contributors in this release?
  - one way: git log release-1.14.0.. | grep Author | sort | uniq
  - commit it

- update "docs/known_issues.rst" if appropriate
- update "docs/Installation/install-tahoe.rst" references to the new release
- Push the branch to github
- Create a (draft) PR; this should trigger CI (note that github
  doesn't let you create a PR without some changes on the branch so
  running + committing the NEWS.txt file achieves that without changing
  any code)
- Confirm CI runs successfully on all platforms


Create Release Candidate
````````````````````````

Before "officially" tagging any release, we will make a
release-candidate available. So there will be at least 1.15.0rc0 (for
example). If there are any problems, an rc1 or rc2 etc may also be
released. Anyone can sign these releases (ideally they'd be signed
"officially" as well, but it's better to get them out than to wait for
that).

Typically expert users will be the ones testing release candidates and
they will need to evaluate which contributors' signatures they trust.

- (all steps above are completed)
- sign the release

  - git tag -s -u 0xE34E62D06D0E69CFCA4179FFBDE0D31D68666A7A -m "release Tahoe-LAFS-1.15.0rc0" tahoe-lafs-1.15.0rc0
  - (replace the key-id above with your own)

- build all code locally
  - these should all pass:

    - tox -e py27,codechecks,docs,integration

  - these can fail (ideally they should not of course):

    - tox -e deprecations,upcoming-deprecations

- build tarballs

  - tox -e tarballs
  - confirm it at least exists:
  - ls dist/ | grep 1.15.0rc0

- inspect and test the tarballs

  - install each in a fresh virtualenv
  - run `tahoe` command

- when satisfied, sign the tarballs:

  - gpg --pinentry=loopback --armor --detach-sign dist/tahoe_lafs-1.15.0rc0-py2-none-any.whl
  - gpg --pinentry=loopback --armor --detach-sign dist/tahoe_lafs-1.15.0rc0.tar.bz2
  - gpg --pinentry=loopback --armor --detach-sign dist/tahoe_lafs-1.15.0rc0.tar.gz
  - gpg --pinentry=loopback --armor --detach-sign dist/tahoe_lafs-1.15.0rc0.zip


Privileged Contributor
-----------------------

Steps in this portion require special access to keys or
infrastructure. For example, **access to tahoe-lafs.org** to upload
binaries or edit HTML.


Hack Tahoe-LAFS
```````````````

Did anyone contribute a hack since the last release? If so, then
https://tahoe-lafs.org/hacktahoelafs/ needs to be updated.


Sign Git Tag
````````````

- git tag -s -u 0xE34E62D06D0E69CFCA4179FFBDE0D31D68666A7A -m "release Tahoe-LAFS-X.Y.Z" tahoe-lafs-X.Y.Z


Upload Artifacts
````````````````

Any release-candidate or actual release plus signature (.asc file)
need to be uploaded to https://tahoe-lafs.org in `~source/downloads`

- secure-copy all release artifacts to the download area on the
  tahoe-lafs.org host machine. `~source/downloads` on there maps to
  https://tahoe-lafs.org/downloads/ on the Web.
- scp dist/*1.15.0* username@tahoe-lafs.org:/home/source/downloads
- the following developers have access to do this:

  - exarkun
  - meejah
  - warner

For the actual release, the tarball and signature files need to be
uploaded to PyPI as well.

- how to do this?
- (original guide says only `twine upload dist/*`)
- the following developers have access to do this:

  - warner
  - exarkun (partial?)
  - meejah (partial?)

Announcing the Release Candidate
````````````````````````````````

The release-candidate should be announced by posting to the
mailing-list (tahoe-dev@lists.tahoe-lafs.org). For example:
https://lists.tahoe-lafs.org/pipermail/tahoe-dev/2020-October/009978.html


Is The Release Done Yet?
````````````````````````

If anyone reports a problem with a release-candidate then a new
release-candidate should be made once a fix has been merged to
master. Repeat the above instructions with `rc1` or `rc2` or whatever
is appropriate.

Once a release-candidate has marinated for some time then it can be
made into a the actual release.

The actual release follows the same steps as above, with some differences:

- there is no "-rcX" on the end of release names
- the release is uploaded to PyPI (using Twine)
- the version is tagged in Git (ideally using "the tahoe release key"
  but can be done with any of the authorized core developers' personal
  key)
- the release-candidate branches must be merged back to master after
  the release is official (e.g. causing newsfragments to be deleted on
  master, etc)


Announcing the Release
----------------------


mailing-lists
`````````````

A new Tahoe release is traditionally announced on our mailing-list
(tahoe-dev@tahoe-lafs.org). The former version of these instructions
also announced the release on the following other lists:

- tahoe-announce@tahoe-lafs.org
- twisted-python@twistedmatrix.com
- liberationtech@lists.stanford.edu
- lwn@lwn.net
- p2p-hackers@lists.zooko.com
- python-list@python.org
- http://listcultures.org/pipermail/p2presearch_listcultures.org/
- cryptopp-users@googlegroups.com


wiki
````

Edit the "News" section of the front page of https://tahoe-lafs.org
with a link to the mailing-list archive of the announcement message.
