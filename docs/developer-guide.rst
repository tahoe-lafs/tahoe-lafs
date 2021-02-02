Developer Guide
===============


Pre-commit Checks
-----------------

This project is configured for use with `pre-commit`_ to install `VCS/git hooks`_ which perform some static code analysis checks and other code checks to catch common errors.
These hooks can be configured to run before commits or pushes

For example::

  tahoe-lafs $ pre-commit install --hook-type pre-push
  pre-commit installed at .git/hooks/pre-push
  tahoe-lafs $ echo "undefined" > src/allmydata/undefined_name.py
  tahoe-lafs $ git add src/allmydata/undefined_name.py
  tahoe-lafs $ git commit -a -m "Add a file that violates flake8"
  tahoe-lafs $ git push
  codechecks...............................................................Failed
  - hook id: codechecks
  - exit code: 1

  GLOB sdist-make: ./tahoe-lafs/setup.py
  codechecks inst-nodeps: ...
  codechecks installed: ...
  codechecks run-test-pre: PYTHONHASHSEED='...'
  codechecks run-test: commands[0] | flake8 src/allmydata/undefined_name.py
  src/allmydata/undefined_name.py:1:1: F821 undefined name 'undefined'
  ERROR: InvocationError for command ./tahoe-lafs/.tox/codechecks/bin/flake8 src/allmydata/undefined_name.py (exited with code 1)
  ___________________________________ summary ____________________________________
  ERROR:   codechecks: commands failed

To uninstall::

  tahoe-lafs $ pre-commit uninstall --hook-type pre-push
  pre-push uninstalled



.. _`pre-commit`: https://pre-commit.com
.. _`VCS/git hooks`: `pre-commit`_
.. _`pre-commit configuration`: ../.pre-commit-config.yaml
