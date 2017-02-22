
# NOTE: this Makefile requires GNU make

default:
	@echo "no default target"

PYTHON=python
export PYTHON
PYFLAKES=pyflakes
export PYFLAKES

SOURCES=src/allmydata static misc setup.py
APPNAME=tahoe-lafs

# This is necessary only if you want to automatically produce a new
# _version.py file from the current git history (without doing a build).
.PHONY: make-version
make-version:
	$(PYTHON) ./setup.py update_version

.built:
	$(MAKE) build

src/allmydata/_version.py:
	$(MAKE) make-version

# Build OS X pkg packages.
.PHONY: build-osx-pkg test-osx-pkg upload-osx-pkg
build-osx-pkg:
	misc/build_helpers/build-osx-pkg.sh $(APPNAME)

test-osx-pkg:
	$(PYTHON) misc/build_helpers/test-osx-pkg.py

upload-osx-pkg:
	@echo "uploading to ~tahoe-tarballs/OS-X-packages/ via flappserver"
	@if [ "X${BB_BRANCH}" = "Xmaster" ] || [ "X${BB_BRANCH}" = "X" ]; then \
	  flappclient --furlfile ~/.tahoe-osx-pkg-upload.furl upload-file tahoe-lafs-*-osx.pkg; \
	 else \
	  echo not uploading tahoe-lafs-osx-pkg because this is not trunk but is branch \"${BB_BRANCH}\" ; \
	fi

.PHONY: smoketest
smoketest:
	-python ./src/allmydata/test/check_magicfolder_smoke.py kill
	-rm -rf smoke_magicfolder/
	python ./src/allmydata/test/check_magicfolder_smoke.py

# code coverage-based testing is disabled temporarily, as we switch to tox.
# This will eventually be added to a tox environment. The following comments
# and variable settings are retained as notes for that future effort.

## # code coverage: install the "coverage" package from PyPI, do "make
## # test-coverage" to do a unit test run with coverage-gathering enabled, then
## # use "make coverage-output" to generate an HTML report. Also see "make
## # .coverage.el" and misc/coding_tools/coverage.el for Emacs integration.
##
## # This might need to be python-coverage on Debian-based distros.
## COVERAGE=coverage
##
## COVERAGEARGS=--branch --source=src/allmydata
##
## # --include appeared in coverage-3.4
## COVERAGE_OMIT=--include '$(CURDIR)/src/allmydata/*' --omit '$(CURDIR)/src/allmydata/test/*'


.PHONY: code-checks
#code-checks: build version-and-path check-interfaces check-miscaptures -find-trailing-spaces -check-umids pyflakes
code-checks: check-interfaces check-debugging check-miscaptures -find-trailing-spaces -check-umids pyflakes

.PHONY: check-interfaces
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
# developer. But as a hint, after running 'tox', ./.tox/py27/bin/python will
# probably work.

# src/allmydata/test/bench_dirnode.py


# The check-speed and check-grid targets are disabled, since they depend upon
# the pre-located $(TAHOE) executable that was removed when we switched to
# tox. They will eventually be resurrected as dedicated tox environments.

# The check-speed target uses a pre-established client node to run a canned
# set of performance tests against a test network that is also
# pre-established (probably on a remote machine). Provide it with the path to
# a local directory where this client node has been created (and populated
# with the necessary FURLs of the test network). This target will start that
# client with the current code and then run the tests. Afterwards it will
# stop the client.
#
# The 'sleep 5' is in there to give the new client a chance to connect to its
# storageservers, since check_speed.py has no good way of doing that itself.

##.PHONY: check-speed
##check-speed: .built
##	if [ -z '$(TESTCLIENTDIR)' ]; then exit 1; fi
##	@echo "stopping any leftover client code"
##	-$(TAHOE) stop $(TESTCLIENTDIR)
##	$(TAHOE) start $(TESTCLIENTDIR)
##	sleep 5
##	$(TAHOE) @src/allmydata/test/check_speed.py $(TESTCLIENTDIR)
##	$(TAHOE) stop $(TESTCLIENTDIR)

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
	rm -rf build _trial_temp _test_memory .built
	rm -f `find src *.egg -name '*.so' -or -name '*.pyc'`
	rm -rf support dist
	rm -rf `ls -d *.egg | grep -vEe"setuptools-|setuptools_darcs-|darcsver-"`
	rm -rf *.pyc
	rm -f bin/tahoe bin/tahoe.pyscript
	rm -f *.pkg

.PHONY: distclean
distclean: clean
	rm -rf src/*.egg-info
	rm -f src/allmydata/_version.py
	rm -f src/allmydata/_appname.py


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
tarballs:
	$(MAKE) make-version
	$(PYTHON) setup.py sdist --formats=bztar,gztar,zip bdist_wheel

.PHONY: upload-tarballs
upload-tarballs:
	@if [ "X${BB_BRANCH}" = "Xmaster" ] || [ "X${BB_BRANCH}" = "X" ]; then for f in dist/*; do flappclient --furlfile ~/.tahoe-tarball-upload.furl upload-file $$f; done ; else echo not uploading tarballs because this is not trunk but is branch \"${BB_BRANCH}\" ; fi
