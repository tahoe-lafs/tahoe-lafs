Developer Guide
===============


Pre-commit Checks
-----------------

This project is configured for use with `pre-commit <https://pre-commit.com>`_ to perform some static code analysis checks. By default, pre-commit behavior is disabled. To enable pre-commit in a local checkout, first install pre-commit (consider using `pipx <https://pipxproject.github.io/pipx/>`_), then install the hooks with ``pre-commit install``.

For example::

	tahoe-lafs $ pre-commit install
	pre-commit installed at .git/hooks/pre-commit
	tahoe-lafs $ echo "def foo():\n\t''" > src/allmydata/tabbed.py
	tahoe-lafs $ git add src/allmydata/tabbed.py
	tahoe-lafs $ git commit -a -m "Add a file that violates flake8"
	flake8...................................................................Failed
	- hook id: flake8
	- exit code: 1

	src/allmydata/tabbed.py:2:1: W191 indentation contains tabs

To uninstall::

	tahoe-lafs $ pre-commit uninstall
	pre-commit uninstalled


Some find running linters on every commit to be a nuisance. To avoid the checks triggering during commits, but to check before pushing to the CI, install the hook for pre-push instead::

	tahoe-lafs $ pre-commit install -t pre-push
	pre-commit installed at .git/hooks/pre-push
	tahoe-lafs $ git commit -a -m "Add a file that violates flake8"
	[3398.pre-commit 29f8f43d2] Add a file that violates flake8
	 1 file changed, 2 insertions(+)
	 create mode 100644 src/allmydata/tabbed.py
	tahoe-lafs $ git push
	flake8...................................................................Failed
	- hook id: flake8
	- exit code: 1

	src/allmydata/tabbed.py:2:1: W191 indentation contains tabs

	error: failed to push some refs to 'github.com:jaraco/tahoe-lafs.git'
