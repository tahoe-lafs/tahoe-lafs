
=================
Release Checklist
=================

This release checklist specifies a series of checks that anyone engaged in
releasing a version of Tahoe should follow.

Any contributor can do the first part of the release preparation. Only
certain contributors can perform other parts. These are the two main
sections of this checklist (and could be done by different people).

A final section describes how to announce the release.

This checklist is based on the original instructions (in old revisions in the file
`docs/how_to_make_a_tahoe-lafs_release.org`).


Automated releases
==================

It's possible to automatically complete the instructions in this document by running the `release script <../release.py>`__

Usage :
 - `python release.py --help` => Help menu
 - `python release.py --tag 1.18.0 --ticket 3549 --clean --ignore-deps` => Make release `1.18.0` with ticket number `3549`, skip dependency checks (`--ignore-deps`), clean old release files (--clean)
 - `python release.py --tag 1.18.0 --ticket 3549 --clean --ignore-deps --sign YOUR_KEY_HERE --repo git@github.com:your-user/tahoe-lafs.git` => Include signing key and repository

 **NB** : You might want to push the release branch to a fork, in such a case please set the `--repo` flag.


--------------------------------------------------


Any Contributor
===============

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
- Making first release? See `GPG Setup Instructions <gpg-setup.rst>`__ to make sure you can sign releases. [One time setup]

Get a clean checkout
````````````````````

The release proccess involves compressing source files and putting them in formats
suitable for distribution such as ``.tar.gz`` and ``zip``. That said, it's neccesary to
the release process begins with a clean checkout to avoid making a release with
previously generated files.

- Inside the tahoe root dir run ``git clone . ../tahoe-release-x.x.x`` where (x.x.x is the release number such as 1.16.0).

.. note::
     The above command would create a new directory at the same level as your original clone named ``tahoe-release-x.x.x``. You can name this folder however you want but it would be a good
     practice to give it the release name. You MAY also discard this directory once the release
     process is complete.

Get into the release directory and install dependencies by running:

- cd ../tahoe-release-x.x.x (assuming you are still in your original clone)
- python -m venv venv
- ./venv/bin/pip install --editable .[test]


Create Branch and Apply Updates
```````````````````````````````

- Create a branch for the release/candidate: git checkout -b XXXX.release-1.16.0
- produce a new NEWS.txt file (this does a commit): tox -e news
- create the news for the release:
  - touch newsfragments/<ticket number>.minor
  - git add newsfragments/<ticket number>.minor
  - git commit -m news

- manually fix ``NEWS.txt``:
  - proper title for latest release ("Release 1.16.0" instead of "Release ...post1432")
  - double-check date (maybe release will be in the future)
  - spot-check the release notes (these come from the newsfragments files though so don't do heavy editing)
  - commit these changes

- update ``relnotes.txt``:
  - update all mentions of ``1.16.0`` to new and higher release version for example ``1.16.1``
  - update "previous release" statement and date
  - summarize major changes
  - commit it

- update ``nix/tahoe-lafs.nix``:
  - change the value given for `version` from `OLD.post1` to `NEW.post1`

- update ``docs/known_issues.rst`` if appropriate
- Push the branch to github

- Create a (draft) PR; this should trigger CI (note that github
  doesn't let you create a PR without some changes on the branch so
  running + committing the NEWS.txt file achieves that without changing
  any code)
- Confirm CI runs successfully on all platforms


Create The Release
``````````````````

- build all code locally

  - these should all pass:

    - tox -e py311,codechecks,docs,integration

  - these can fail (ideally they should not of course):

    - tox -e deprecations,upcoming-deprecations

- install build dependencies

    - pip install -e .[build]

- build tarball + wheel (should be built into dist/)

    - hatchling build

- inspect and test the tarballs

    - install each in a fresh virtualenv
    - run `tahoe` command

- when satisfied, sign the tarballs:

  - gpg --pinentry=loopback --armor -u 0xE34E62D06D0E69CFCA4179FFBDE0D31D68666A7A --detach-sign dist/tahoe_lafs-1.20.0rc0-py2.py3-none-any.whl
  - gpg --pinentry=loopback --armor --detach-sign dist/tahoe_lafs-1.20.0rc0.tar.gz


Privileged Contributor
======================

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

Any release plus signature (.asc file) need to be uploaded to
https://tahoe-lafs.org in `~source/downloads`

- secure-copy all release artifacts to the download area on the
  tahoe-lafs.org host machine. `~source/downloads` on there maps to
  https://tahoe-lafs.org/downloads/ on the Web:

    - scp dist/*1.20.0* username@tahoe-lafs.org:/home/source/downloads

- the following developers have access to do this:

  - exarkun
  - meejah
  - warner

Push the signed tag to the main repository:

- git push origin tahoe-lafs-1.20.0

For the actual release, the tarball and signature files need to be
uploaded to PyPI as well.

- ls dist/*1.20.0*
- twine upload --username __token__ --password `cat SECRET-pypi-tahoe-publish-token` dist/*1.20.0*

The following developers have access to do this:

  - warner
  - meejah
  - exarkun (partial?)


Merge the Release Branch
````````````````````````

Once the release has been signed and uploaded the release branch
should be merged to master (thus deleting newsfragments, etc).


Announcing the Release
``````````````````````

The release-candidate should be announced by posting to the
mailing-list (tahoe-dev@lists.tahoe-lafs.org).


mailing-lists
`````````````

A new Tahoe release is traditionally announced on our mailing-list
(tahoe-dev@lists.tahoe-lafs.org).  For example:
https://lists.tahoe-lafs.org/pipermail/tahoe-dev/2020-October/009978.html

The former version of these instructions also announced the release on
the following other lists:

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
