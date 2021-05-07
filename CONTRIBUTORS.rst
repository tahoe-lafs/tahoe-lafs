Contributor Checklist
=====================


* Create a ``Trac`` ticket, fill it out and assign it to yourself (contact exarkun if you don't have an account):

  ``https://tahoe-lafs.org/trac/tahoe-lafs/newticket``

* Use the ticket number to name your branch (example): 

  ``3003.contributor-guide``

* Good idea to add tests at the same time you write your code.

* Add a file to the ``/newsfragments`` folder, named with the ticket number and the type of patch (example):

  ``newsfragments/3651.minor``

* ``towncrier`` recognizes the following types:

  ``incompat``, ``feature``, ``bugfix``, ``installation``, ``configuration``, ``documentation``, ``removed``, ``other``, ``minor``
* Add one sentence to ``newsfragments/<ticket-number>.<towncrier-type>`` describing the change (example):

  ``The integration test suite has been updated to use pytest-twisted instead of deprecated pytest APIs.``

* Run the test suite with ``tox``, ``tox -e codechecks`` and ``tox -e typechecks``

* Push your branch to Github with your ticket number in the merge commit message (example):

  ``Fixes ticket:3003``

  This makes the ``Trac`` ticket close when your PR gets approved.

* Request appropriate review - we suggest asking `Tahoe Committers <https://github.com/orgs/tahoe-lafs/teams/tahoe-committers>`__

References
----------

This checklist is a summary of `this page on contributing Patches <https://tahoe-lafs.org/trac/tahoe-lafs/wiki/Patches>`__ 

Before authoring or reviewing a patch, please familiarize yourself with the `Coding Standard <https://tahoe-lafs.org/trac/tahoe-lafs/wiki/CodingStandards>`__ 
and the `Contributor Code of Conduct <docs/CODE_OF_CONDUCT.md>`__.
