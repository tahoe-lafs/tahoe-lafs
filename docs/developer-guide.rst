Developer Guide
===============


Pre-commit Checks
-----------------

This project is configured for use with `pre-commit`_ to install `VCS/git hooks`_ which
perform some static code analysis checks and other code checks to catch common errors
before each commit and to run the full self-test suite to find less obvious regressions
before each push to a remote.

For example::

  tahoe-lafs $ make install-vcs-hooks
  ...
  + ./.tox//py36/bin/pre-commit install --hook-type pre-commit
  pre-commit installed at .git/hooks/pre-commit
  + ./.tox//py36/bin/pre-commit install --hook-type pre-push
  pre-commit installed at .git/hooks/pre-push
  tahoe-lafs $ python -c "import pathlib; pathlib.Path('src/allmydata/tabbed.py').write_text('def foo():\\n\\tpass\\n')"
  tahoe-lafs $ git add src/allmydata/tabbed.py
  tahoe-lafs $ git commit -a -m "Add a file that violates flake8"
  ...
  codechecks...............................................................Failed
  - hook id: codechecks
  - exit code: 1

  GLOB sdist-make: ./tahoe-lafs/setup.py
  codechecks inst-nodeps: ...
  codechecks installed: ...
  codechecks run-test-pre: PYTHONHASHSEED='...'
  codechecks run-test: commands[0] | flake8 src static misc setup.py
  src/allmydata/tabbed.py:2:1: W191 indentation contains tabs
  ERROR: InvocationError for command ./tahoe-lafs/.tox/codechecks/bin/flake8 src static misc setup.py (exited with code 1)
  ___________________________________ summary ____________________________________
  ERROR:   codechecks: commands failed
  ...

To uninstall::

  tahoe-lafs $ make uninstall-vcs-hooks
  ...
  + ./.tox/py36/bin/pre-commit uninstall
  pre-commit uninstalled
  + ./.tox/py36/bin/pre-commit uninstall -t pre-push
  pre-push uninstalled

Note that running the full self-test suite takes several minutes so expect pushing to
take some time.  If you can't or don't want to wait for the hooks in some cases, use the
``--no-verify`` option to ``$ git commit ...`` or ``$ git push ...``.  Alternatively,
see the `pre-commit`_ documentation and CLI help output and use the committed
`pre-commit configuration`_ as a starting point to write a local, uncommitted
``../.pre-commit-config.local.yaml`` configuration to use instead.  For example::

  tahoe-lafs $ ./.tox/py36/bin/pre-commit --help
  tahoe-lafs $ ./.tox/py36/bin/pre-commit instll --help
  tahoe-lafs $ cp  "./.pre-commit-config.yaml" "./.pre-commit-config.local.yaml"
  tahoe-lafs $ editor "./.pre-commit-config.local.yaml"
  ...
  tahoe-lafs $ ./.tox/py36/bin/pre-commit install -c "./.pre-commit-config.local.yaml" -t pre-push
  pre-commit installed at .git/hooks/pre-push
  tahoe-lafs $ git commit -a -m "Add a file that violates flake8"
  [3398.pre-commit 29f8f43d2] Add a file that violates flake8
   1 file changed, 2 insertions(+)
   create mode 100644 src/allmydata/tabbed.py
  tahoe-lafs $ git push
  ...
  codechecks...............................................................Failed
  - hook id: codechecks
  - exit code: 1

  GLOB sdist-make: ./tahoe-lafs/setup.py
  codechecks inst-nodeps: ...
  codechecks installed: ...
  codechecks run-test-pre: PYTHONHASHSEED='...'
  codechecks run-test: commands[0] | flake8 src static misc setup.py
  src/allmydata/tabbed.py:2:1: W191 indentation contains tabs
  ERROR: InvocationError for command ./tahoe-lafs/.tox/codechecks/bin/flake8 src static misc setup.py (exited with code 1)
  ___________________________________ summary ____________________________________
  ERROR:   codechecks: commands failed
  ...

  error: failed to push some refs to 'github.com:jaraco/tahoe-lafs.git'


.. _`pre-commit`: https://pre-commit.com
.. _`VCS/git hooks`: `pre-commit`_
.. _`pre-commit configuration`: ../.pre-commit-config.yaml
