
=================
Release Checklist
=================

This document specifies how to do a Tahoe-LAFS release.

We divide the document into two sections:
the first section may be accomplished by an contributor;
the tasks in the second section require special credeitals (usually held by release managers).

For historical instructions, please look for old versions of `docs/how_to_make_a_tahoe-lafs_release.org`).


Required Tools
==============

You must have GnuPG set up for certain tasks; see `GPG Setup Instructions <gpg-setup.rst>`_ for details.


Any Contributor
===============

Anyone who can create normal PRs should be able to complete this portion of the release process.


Prepare for the Release
```````````````````````

The ``master`` branch should always be releasable.

It may be worth asking (on IRC or mailing-ist) if anything will be merged imminently (for example, "I will prepare a release this coming Tuesday if you want to get anything in").

- Create a ticket for the release in Trac: https://tahoe-lafs.org/trac/tahoe-lafs/newticket
- Remember the ticket number: ``export RELEASE_TICKET=4076``


Get a clean checkout
````````````````````

The release proccess involves compressing source files and putting them in formats suitable for distribution such as ``.tar.gz`` and ``zip``.
To minimize surprises (extra files, etc), we begin the release process with a clean checkout.

- Make sure our checkout is up-to-date: git checkout master; git pull
- Clone a local, clean checkout: git clone . ../tahoe-release-$(RELEASE_TICKET)

.. note:: 
     The above command would create a new directory at the same level as your original clone.
     You can name this folder however you want but it would be a good practice to give it the release name.
     You SHOULD also discard this directory once the release process is complete.

Get into the release directory and install dependencies:

- cd ../tahoe-release-$(RELEASE_TICKET)
- python -m venv venv
- ./venv/bin/pip install --editable .[test]


Create Branch and Apply Updates
```````````````````````````````

- Remember the new version: export RELEASE_VERSION=1.19.0
- Create a branch for the release/candidate: git chheckout -b $(RELEASE_TICKET):release-$(RELEASE_VERSION)
- Produce *and commit* recent changes: run tox -e news

Update the release notes:

- manually fix NEWS.txt:
  - proper title for latest release ("Release 1.16.0" instead of "Release ...post1432")
  - double-check date (maybe release will be in the future)
  - spot-check the release notes (these come from the newsfragments files though so don't do heavy editing)
  - commit these changes

- update "relnotes.txt"
  - update all mentions of ``1.19.0`` to new and higher release version for example ``1.19.1``
  - update "previous release" statement and date
  - summarize major changes
  - commit it

- update "nix/tahoe-lafs.nix"
  - change the value given for `version` from `OLD.post1` to `NEW.post1`

- update "docs/known_issues.rst" if appropriate
- Push the branch to github
- Create a (draft) PR; this should trigger CI (note that github doesn't let you create a PR without some changes on the branch so running + committing the NEWS.txt file achieves that without changing  any code)
- Confirm CI runs successfully on all platforms


Create Release Candidate
````````````````````````

Before "officially" tagging any release, we will make a release-candidate available.
So there will be at least 1.19.0rc0 (for example).
If there are any problems, an rc1 or rc2 etc may also be released.
Anyone can sign these releases (ideally they'd be signed "officially" as well, but it's better to get them out than to wait for that).

Typically expert users will be the ones testing release candidates and they will need to evaluate which contributors' signatures they trust.

- (all steps above are completed)
- sign the release: git tag -s -u 0xE34E62D06D0E69CFCA4179FFBDE0D31D68666A7A -m "release Tahoe-LAFS-${RELEASE_VERSION}rc0" tahoe-lafs-${RELEASE_VERSION}rc0

.. note::
    Replace the key-id above with your own, which can simply be your email if it's attached to your fingerprint.

- build all code locally.
  - these should all pass:
    - tox -e py37,codechecks,docs,integration

  - these can fail (ideally they should not of course):
    - tox -e deprecations,upcoming-deprecations

- build tarballs: tox -e tarballs
- Confirm: ls dist/ | grep 1.16.0rc0
- inspect and test the tarballs
  - install each in a fresh virtualenv
  - run ``tahoe`` command (as bare minimum)

- when satisfied, sign the tarballs:
  - gpg --pinentry=loopback --armor --detach-sign dist/tahoe_lafs-${RELEASE_VERSION}rc0-py2.py3-none-any.whl
  - gpg --pinentry=loopback --armor --detach-sign dist/tahoe_lafs-${RELEASE_VERSION}rc0.tar.gz


Privileged Contributor
======================

Steps in this portion require special access to keys or infrastructure:
- access to ``tahoe-lafs.org`` to upload binaries or edit HTML.
- a qualified public GnuPG key to sign releases (see `GPG Setup Instructions <gpg-setup.rst>`_)
- access to PyPI to upload the release
- a "token" from PyPI authorizing upload

The following developers currently have access to do this:
- exarkun
- meejah
- warner

If you do not yet have an upload token from PyPI:
- log in to https://pypi.org
- click on your avatar/username dropdown
- click on "Your projects"
- find ``tahoe-lafs``
- click on the "Manage" button for tahoe-lafs
- click on "Settings"
- click on the "Create a token for tahoe-lafs" button (under "API Tokens")
- copy-paste the token into ``PRIVATE-release-token`` in your Tahoe-LAFS checkout


Hack Tahoe-LAFS
```````````````

Did anyone contribute a hack since the last release?
If so, then https://tahoe-lafs.org/hacktahoelafs/ needs to be updated.


Sign Git Tag
````````````
- git tag -s -u 0xE34E62D06D0E69CFCA4179FFBDE0D31D68666A7A -m "release Tahoe-LAFS-${RELEASE_VERSION}" tahoe-lafs-${RELEASE_VERSION}


Upload Artifacts
````````````````

Any release-candidate or actual release plus signature (.asc file) need to be uploaded to https://tahoe-lafs.org in ``~source/downloads``.

Secure-copy all release artifacts to the download area on the tahoe-lafs.org host machine:
- ``~source/downloads`` on there maps to https://tahoe-lafs.org/downloads/ on the Web:
- scp dist/*${RELEASE_VERSION}* username@tahoe-lafs.org:/home/source/downloads

Push the signed tag to the main repository:
- git push origin tahoe-lafs-$(RELEASE_VERSION)

For an actual release, the tarball and signature files need to be uploaded to PyPI as well.
In 2023 and forward, PyPI requires us to use tokens to upload.
Perform the upload:
- twine upload --username __token__ --password $(cat PRIVATE-release-token) dist/*${RELEASE_VERSION}*


Announcing the Release Candidate
````````````````````````````````

The release-candidate should be announced by posting to the mailing-list (tahoe-dev@lists.tahoe-lafs.org).
For example: https://lists.tahoe-lafs.org/pipermail/tahoe-dev/2020-October/009978.html


Is The Release Done Yet?
````````````````````````

If anyone reports a problem with a release-candidate then a new release-candidate should be made once a fix has been merged to master.
Repeat the above instructions with `rc1` or `rc2` or whatever is appropriate.

Once a release-candidate has marinated for some time then it can be made into a the actual release.

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

A new Tahoe release is traditionally announced on our mailing-list (tahoe-dev@tahoe-lafs.org).
The former version of these instructions also announced the release on the following other lists:

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

Edit the "News" section of the front page of https://tahoe-lafs.org with a link to the mailing-list archive of the announcement message.
