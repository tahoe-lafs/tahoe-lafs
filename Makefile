
# NOTE: this Makefile requires GNU make

default: build

PYTHON=python
export PYTHON

# setup.py will extend sys.path to include our support/lib/... directory
# itself. It will also create it in the beginning of the 'develop' command.

TAHOE=$(PYTHON) bin/tahoe
SOURCES=src/allmydata src/buildtest static misc bin/tahoe-script.template setup.py
APPNAME=allmydata-tahoe

# This is necessary only if you want to automatically produce a new
# _version.py file from the current git history (without doing a build).
.PHONY: make-version
make-version:
	$(PYTHON) ./setup.py update_version

.built:
	$(MAKE) build

src/allmydata/_version.py:
	$(MAKE) make-version

# It is unnecessary to have this depend on build or src/allmydata/_version.py,
# since 'setup.py build' always updates the version.
.PHONY: build
build:
	$(PYTHON) setup.py build
	touch .built

# Build OS X pkg packages.
.PHONY: build-osx-pkg test-osx-pkg upload-osx-pkg
build-osx-pkg: build
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

# TESTING

# you can use 'make test TEST=allmydata.test.test_introducer' to run just
# test_introducer. TEST=allmydata.test.test_client.Basic.test_permute works
# too.
TEST=allmydata

# It is unnecessary to have this depend on build or src/allmydata/_version.py,
# since 'setup.py test' always updates the version and builds before testing.
.PHONY: test
test:
	$(PYTHON) setup.py test $(TRIALARGS) -s $(TEST)
	touch .built

.PHONY: check
check: test

.PHONY: quicktest
quicktest: make-version
	$(TAHOE) debug trial $(TRIALARGS) $(TEST)

# "make tmpfstest" may be a faster way of running tests on Linux. It works best when you have
# at least 330 MiB of free physical memory (to run the whole test suite). Since it uses sudo
# to mount/unmount the tmpfs filesystem, it might prompt for your password.
.PHONY: tmpfstest
tmpfstest:
	time make _tmpfstest 'TMPDIR=$(shell mktemp -d --tmpdir=.)'

.PHONY: _tmpfstest
_tmpfstest: make-version
	sudo mount -t tmpfs -o size=400m tmpfs '$(TMPDIR)'
	-$(TAHOE) debug trial --rterrors '--temp-directory=$(TMPDIR)/_trial_temp' $(TRIALARGS) $(TEST)
	sudo umount '$(TMPDIR)'
	rmdir '$(TMPDIR)'

.PHONY: smoketest
smoketest:
	-python ./src/allmydata/test/check_magicfolder_smoke.py kill
	-rm -rf smoke_magicfolder/
	python ./src/allmydata/test/check_magicfolder_smoke.py

# code coverage: install the "coverage" package from PyPI, do "make test-coverage" to
# do a unit test run with coverage-gathering enabled, then use "make coverage-output" to
# generate an HTML report. Also see "make .coverage.el" and misc/coding_tools/coverage.el
# for Emacs integration.

# This might need to be python-coverage on Debian-based distros.
COVERAGE=coverage

COVERAGEARGS=--branch --source=src/allmydata

# --include appeared in coverage-3.4
COVERAGE_OMIT=--include '$(CURDIR)/src/allmydata/*' --omit '$(CURDIR)/src/allmydata/test/*'

.PHONY: test-coverage
test-coverage: build
	rm -f .coverage
	$(TAHOE) '@$(COVERAGE)' run $(COVERAGEARGS) @tahoe debug trial $(TRIALARGS) $(TEST)

.PHONY: coverage-output
coverage-output:
	rm -rf coverage-html
	coverage html -i -d coverage-html $(COVERAGE_OMIT)
	cp .coverage coverage-html/coverage.data
	@echo "now point your browser at coverage-html/index.html"

.coverage.el: .coverage
	$(PYTHON) misc/coding_tools/coverage2el.py


.PHONY: code-checks
code-checks: build version-and-path check-interfaces check-miscaptures -find-trailing-spaces -check-umids pyflakes

.PHONY: version-and-path
version-and-path:
	$(TAHOE) --version-and-path

.PHONY: check-interfaces
check-interfaces:
	$(TAHOE) @misc/coding_tools/check-interfaces.py 2>&1 |tee violations.txt
	@echo

.PHONY: check-miscaptures
check-miscaptures:
	$(PYTHON) misc/coding_tools/check-miscaptures.py $(SOURCES) 2>&1 |tee miscaptures.txt
	@echo

.PHONY: pyflakes
pyflakes:
	@$(PYTHON) -OOu `which pyflakes` $(SOURCES) |sort |uniq
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

.PHONY: check-memory
check-memory: .built
	rm -rf _test_memory
	$(TAHOE) @src/allmydata/test/check_memory.py upload
	$(TAHOE) @src/allmydata/test/check_memory.py upload-self
	$(TAHOE) @src/allmydata/test/check_memory.py upload-POST
	$(TAHOE) @src/allmydata/test/check_memory.py download
	$(TAHOE) @src/allmydata/test/check_memory.py download-GET
	$(TAHOE) @src/allmydata/test/check_memory.py download-GET-slow
	$(TAHOE) @src/allmydata/test/check_memory.py receive

.PHONY: check-memory-once
check-memory-once: .built
	rm -rf _test_memory
	$(TAHOE) @src/allmydata/test/check_memory.py $(MODE)

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

.PHONY: check-speed
check-speed: .built
	if [ -z '$(TESTCLIENTDIR)' ]; then exit 1; fi
	@echo "stopping any leftover client code"
	-$(TAHOE) stop $(TESTCLIENTDIR)
	$(TAHOE) start $(TESTCLIENTDIR)
	sleep 5
	$(TAHOE) @src/allmydata/test/check_speed.py $(TESTCLIENTDIR)
	$(TAHOE) stop $(TESTCLIENTDIR)

# The check-grid target also uses a pre-established client node, along with a
# long-term directory that contains some well-known files. See the docstring
# in src/allmydata/test/check_grid.py to see how to set this up.
.PHONY: check-grid
check-grid: .built
	if [ -z '$(TESTCLIENTDIR)' ]; then exit 1; fi
	$(TAHOE) @src/allmydata/test/check_grid.py $(TESTCLIENTDIR) bin/tahoe

.PHONY: bench-dirnode
bench-dirnode: .built
	$(TAHOE) @src/allmydata/test/bench_dirnode.py

# the provisioning tool runs as a stand-alone webapp server
.PHONY: run-provisioning-tool
run-provisioning-tool: .built
	$(TAHOE) @misc/operations_helpers/provisioning/run.py

# 'make repl' is a simple-to-type command to get a Python interpreter loop
# from which you can type 'import allmydata'
.PHONY: repl
repl:
	$(TAHOE) debug repl

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
	rm -rf misc/dependencies/build misc/dependencies/temp
	rm -rf misc/dependencies/tahoe_deps.egg-info
	rm -f bin/tahoe bin/tahoe.pyscript
	rm -f *.pkg

.PHONY: distclean
distclean: clean
	rm -rf src/allmydata_tahoe.egg-info
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

# The test-desert-island target grabs the tahoe-deps tarball, unpacks it,
# does a build, then asserts that the build did not try to download anything
# as it ran. Invoke this on a new tree, or after a 'clean', to make sure the
# support/lib/ directory is gone.

.PHONY: fetch-and-unpack-deps
fetch-and-unpack-deps:
	test -f tahoe-deps.tar.gz || wget https://tahoe-lafs.org/source/tahoe-lafs/deps/tahoe-lafs-deps.tar.gz
	rm -rf tahoe-deps
	tar xzf tahoe-lafs-deps.tar.gz

.PHONY: test-desert-island
test-desert-island:
	$(MAKE) fetch-and-unpack-deps
	$(MAKE) 2>&1 | tee make.out
	$(PYTHON) misc/build_helpers/check-build.py make.out no-downloads

.PHONY: test-pip-install
test-pip-install:
	$(PYTHON) misc/build_helpers/test-pip-install.py

# TARBALL GENERATION
.PHONY: tarballs
tarballs:
	$(MAKE) make-version
	$(PYTHON) setup.py sdist --formats=bztar,gztar,zip
	$(PYTHON) setup.py sdist --sumo --formats=bztar,gztar,zip

.PHONY: upload-tarballs
upload-tarballs:
	@if [ "X${BB_BRANCH}" = "Xmaster" ] || [ "X${BB_BRANCH}" = "X" ]; then for f in dist/$(APPNAME)-*; do flappclient --furlfile ~/.tahoe-tarball-upload.furl upload-file $$f; done ; else echo not uploading tarballs because this is not trunk but is branch \"${BB_BRANCH}\" ; fi
