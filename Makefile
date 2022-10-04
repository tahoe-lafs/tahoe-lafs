# Tahoe LFS Development and maintenance tasks
#
# NOTE: this Makefile requires GNU make

### Defensive settings for make:
#     https://tech.davis-hansson.com/p/make/
SHELL := bash
.ONESHELL:
.SHELLFLAGS := -xeu -o pipefail -c
.SILENT:
.DELETE_ON_ERROR:
MAKEFLAGS += --warn-undefined-variables
MAKEFLAGS += --no-builtin-rules

# Local target variables
PYTHON=python
export PYTHON
PYFLAKES=flake8
export PYFLAKES
VIRTUAL_ENV=./.tox/py37
SOURCES=src/allmydata static misc setup.py
APPNAME=tahoe-lafs
TEST_SUITE=allmydata


# Top-level, phony targets

.PHONY: default
default:
	@echo "no default target"

.PHONY: test
## Run all tests and code reports
test: .tox/create-venvs.log
# Run codechecks first since it takes the least time to report issues early.
	tox --develop -e codechecks
# Run all the test environments in parallel to reduce run-time
	tox --develop -p auto -e 'py37'
.PHONY: test-venv-coverage
## Run all tests with coverage collection and reporting.
test-venv-coverage:
# Special handling for reporting coverage even when the test run fails
	rm -f ./.coverage.*
	test_exit=
	$(VIRTUAL_ENV)/bin/coverage run -m twisted.trial --rterrors --reporter=timing \
		$(TEST_SUITE) || test_exit="$$?"
	$(VIRTUAL_ENV)/bin/coverage combine
	$(VIRTUAL_ENV)/bin/coverage xml || true
	$(VIRTUAL_ENV)/bin/coverage report
	if [ ! -z "$$test_exit" ]; then exit "$$test_exit"; fi
.PHONY: test-py3-all
## Run all tests under Python 3
test-py3-all: .tox/create-venvs.log
	tox --develop -e py37 allmydata

# This is necessary only if you want to automatically produce a new
# _version.py file from the current git history (without doing a build).
.PHONY: make-version
make-version:
	$(PYTHON) ./setup.py update_version

# Build OS X pkg packages.
.PHONY: build-osx-pkg
build-osx-pkg:
	misc/build_helpers/build-osx-pkg.sh $(APPNAME)

.PHONY: test-osx-pkg
test-osx-pkg:
	$(PYTHON) misc/build_helpers/test-osx-pkg.py

.PHONY: upload-osx-pkg
upload-osx-pkg:
	# [Failure instance: Traceback: <class 'OpenSSL.SSL.Error'>: [('SSL routines', 'ssl3_read_bytes', 'tlsv1 alert unknown ca'), ('SSL routines', 'ssl3_write_bytes', 'ssl handshake failure')]
	#
	# @echo "uploading to ~tahoe-tarballs/OS-X-packages/ via flappserver"
	# @if [ "X${BB_BRANCH}" = "Xmaster" ] || [ "X${BB_BRANCH}" = "X" ]; then \
	#   flappclient --furlfile ~/.tahoe-osx-pkg-upload.furl upload-file tahoe-lafs-*-osx.pkg; \
	#  else \
	#   echo not uploading tahoe-lafs-osx-pkg because this is not trunk but is branch \"${BB_BRANCH}\" ; \
	# fi

.PHONY: code-checks
#code-checks: build version-and-path check-interfaces check-miscaptures -find-trailing-spaces -check-umids pyflakes
code-checks: check-interfaces check-debugging check-miscaptures -find-trailing-spaces -check-umids pyflakes

.PHONY: check-interfaces
check-interfaces:
	$(PYTHON) misc/coding_tools/check-interfaces.py 2>&1 |tee violations.txt
	@echo

.PHONY: check-debugging
check-debugging:
	$(PYTHON) misc/coding_tools/check-debugging.py
	@echo

.PHONY: check-miscaptures
check-miscaptures:
	$(PYTHON) misc/coding_tools/check-miscaptures.py $(SOURCES) 2>&1 |tee miscaptures.txt
	@echo

.PHONY: pyflakes
pyflakes:
	$(PYFLAKES) $(SOURCES) |sort |uniq
	@echo

.PHONY: check-umids
check-umids:
	$(PYTHON) misc/coding_tools/check-umids.py `find $(SOURCES) -name '*.py' -not -name 'old.py'`
	@echo

.PHONY: -check-umids
-check-umids:
	-$(PYTHON) misc/coding_tools/check-umids.py `find $(SOURCES) -name '*.py' -not -name 'old.py'`
	@echo

.PHONY: doc-checks
doc-checks: check-rst

.PHONY: check-rst
check-rst:
	@for x in `find *.rst docs -name "*.rst"`; do rst2html -v $${x} >/dev/null; done 2>&1 |grep -v 'Duplicate implicit target name:'
	@echo

.PHONY: count-lines
count-lines:
	@echo -n "files: "
	@find src -name '*.py' |grep -v /build/ |wc -l
	@echo -n "lines: "
	@cat `find src -name '*.py' |grep -v /build/` |wc -l
	@echo -n "TODO: "
	@grep TODO `find src -name '*.py' |grep -v /build/` | wc -l
	@echo -n "XXX: "
	@grep XXX `find src -name '*.py' |grep -v /build/` | wc -l


# Here is a list of testing tools that can be run with 'python' from a
# virtualenv in which Tahoe has been installed. There used to be Makefile
# targets for each, but the exact path to a suitable python is now up to the
# developer. But as a hint, after running 'tox', ./.tox/py37/bin/python will
# probably work.

# src/allmydata/test/bench_dirnode.py


# The check-grid target also uses a pre-established client node, along with a
# long-term directory that contains some well-known files. See the docstring
# in src/allmydata/test/check_grid.py to see how to set this up.
##.PHONY: check-grid
##check-grid: .built
##	if [ -z '$(TESTCLIENTDIR)' ]; then exit 1; fi
##	$(TAHOE) @src/allmydata/test/check_grid.py $(TESTCLIENTDIR) bin/tahoe

.PHONY: test-get-ignore
test-git-ignore:
	$(MAKE)
	$(PYTHON) misc/build_helpers/test-git-ignore.py

.PHONY: test-clean
test-clean:
	find . |grep -vEe "allfiles.tmp|src/allmydata/_(version|appname).py" |sort >allfiles.tmp.old
	$(MAKE)
	$(MAKE) distclean
	find . |grep -vEe "allfiles.tmp|src/allmydata/_(version|appname).py" |sort >allfiles.tmp.new
	diff allfiles.tmp.old allfiles.tmp.new

# It would be nice if 'make clean' deleted any automatically-generated
# _version.py too, so that 'make clean; make all' could be useable as a
# "what the heck is going on, get me back to a clean state', but we need
# 'make clean' to work on non-checkout trees without destroying useful information.
# Use 'make distclean' instead to delete all generated files.
.PHONY: clean
clean:
	rm -rf build _trial_temp .built
	rm -f `find src *.egg -name '*.so' -or -name '*.pyc'`
	rm -rf support dist
	rm -rf `ls -d *.egg | grep -vEe"setuptools-|setuptools_darcs-|darcsver-"`
	rm -rf *.pyc
	rm -f *.pkg

.PHONY: distclean
distclean: clean
	rm -rf src/*.egg-info
	rm -f src/allmydata/_version.py
	rm -f src/allmydata/_appname.py
	rm -rf ./.tox/


.PHONY: find-trailing-spaces
find-trailing-spaces:
	$(PYTHON) misc/coding_tools/find-trailing-spaces.py -r $(SOURCES)
	@echo

.PHONY: -find-trailing-spaces
-find-trailing-spaces:
	-$(PYTHON) misc/coding_tools/find-trailing-spaces.py -r $(SOURCES)
	@echo

.PHONY: fetch-and-unpack-deps
fetch-and-unpack-deps:
	@echo "test-and-unpack-deps is obsolete"

.PHONY: test-desert-island
test-desert-island:
	@echo "test-desert-island is obsolete"

.PHONY: test-pip-install
test-pip-install:
	@echo "test-pip-install is obsolete"

# TARBALL GENERATION
.PHONY: tarballs
tarballs: # delegated to tox, so setup.py can update setuptools if needed
	tox -e tarballs

.PHONY: upload-tarballs
upload-tarballs:
	@if [ "X${BB_BRANCH}" = "Xmaster" ] || [ "X${BB_BRANCH}" = "X" ]; then for f in dist/*; do flappclient --furlfile ~/.tahoe-tarball-upload.furl upload-file $$f; done ; else echo not uploading tarballs because this is not trunk but is branch \"${BB_BRANCH}\" ; fi


# Real targets

src/allmydata/_version.py:
	$(MAKE) make-version

.tox/create-venvs.log: tox.ini setup.py
	tox --notest -p all | tee -a "$(@)"


# to make a new release:
# - create a ticket for the release in Trac
# - ensure local copy is up-to-date
# - create a branch like "XXXX.release" from up-to-date master
# - in the branch, run "make release"
# - run "make release-test"
# - perform any other sanity-checks on the release
# - run "make release-upload"
# Note that several commands below hard-code "meejah"; if you are
# someone else please adjust them.
release:
	@echo "Is checkout clean?"
	git diff-files --quiet
	git diff-index --quiet --cached HEAD --

	@echo "Clean docs build area"
	rm -rf docs/_build/

	@echo "Install required build software"
	python3 -m pip install --editable .[build]

	@echo "Test README"
	python3 setup.py check -r -s

	@echo "Update NEWS"
	python3 -m towncrier build --yes --version `python3 misc/build_helpers/update-version.py --no-tag`
	git add -u
	git commit -m "update NEWS for release"

# note that this always bumps the "middle" number, e.g. from 1.17.1 -> 1.18.0
# and produces a tag into the Git repository
	@echo "Bump version and create tag"
	python3 misc/build_helpers/update-version.py

	@echo "Build and sign wheel"
	python3 setup.py bdist_wheel
	gpg --pinentry=loopback -u meejah@meejah.ca --armor --detach-sign dist/tahoe_lafs-`git describe | cut -b 12-`-py3-none-any.whl
	ls dist/*`git describe | cut -b 12-`*

	@echo "Build and sign source-dist"
	python3 setup.py sdist
	gpg --pinentry=loopback -u meejah@meejah.ca --armor --detach-sign dist/tahoe-lafs-`git describe | cut -b 12-`.tar.gz
	ls dist/*`git describe | cut -b 12-`*

# basically just a bare-minimum smoke-test that it installs and runs
release-test:
	gpg --verify dist/tahoe-lafs-`git describe | cut -b 12-`.tar.gz.asc
	gpg --verify dist/tahoe_lafs-`git describe | cut -b 12-`-py3-none-any.whl.asc
	virtualenv testmf_venv
	testmf_venv/bin/pip install dist/tahoe_lafs-`git describe | cut -b 12-`-py3-none-any.whl
	testmf_venv/bin/tahoe --version
	rm -rf testmf_venv

release-upload:
	scp dist/*`git describe | cut -b 12-`* meejah@tahoe-lafs.org:/home/source/downloads
	git push origin_push tahoe-lafs-`git describe | cut -b 12-`
	twine upload dist/tahoe_lafs-`git describe | cut -b 12-`-py3-none-any.whl dist/tahoe_lafs-`git describe | cut -b 12-`-py3-none-any.whl.asc dist/tahoe-lafs-`git describe | cut -b 12-`.tar.gz dist/tahoe-lafs-`git describe | cut -b 12-`.tar.gz.asc
