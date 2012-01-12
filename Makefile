
# NOTE: this Makefile requires GNU make

default: build

PYTHON=python
export PYTHON

# setup.py will extend sys.path to include our support/lib/... directory
# itself. It will also create it in the beginning of the 'develop' command.

TAHOE=$(PYTHON) bin/tahoe
SOURCES=src/allmydata src/buildtest static misc/build_helpers bin/tahoe-script.template twisted setup.py

.PHONY: make-version build

# This is necessary only if you want to automatically produce a new
# _version.py file from the current git/darcs history.
make-version:
	$(PYTHON) ./setup.py update_version

.built:
	$(MAKE) build

src/allmydata/_version.py:
	$(MAKE) make-version

# It is unnecessary to have this depend on build or src/allmydata/_version.py,
# since 'setup.py build' always updates the version using 'darcsver --count-all-patches'.
build:
	$(PYTHON) setup.py build
	touch .built

# 'make install' will do the following:
#   build+install tahoe (probably to /usr/lib/pythonN.N/site-packages)
# 'make install PREFIX=/usr/local/stow/tahoe-N.N' will do the same, but to
# a different location

install:
ifdef PREFIX
	mkdir -p $(PREFIX)
	$(PYTHON) ./setup.py install --single-version-externally-managed \
           --prefix=$(PREFIX) --record=./tahoe.files
else
	$(PYTHON) ./setup.py install --single-version-externally-managed
endif


# TESTING

.PHONY: signal-error-deps test check test-coverage quicktest quicktest-coverage
.PHONY: coverage-output get-old-coverage-coverage coverage-delta-output


# you can use 'make test TEST=allmydata.test.test_introducer' to run just
# test_introducer. TEST=allmydata.test.test_client.Basic.test_permute works
# too.
TEST=allmydata

# use 'make test TRIALARGS=--reporter=bwverbose' from buildbot, to
# suppress the ansi color sequences

# It is unnecessary to have this depend on build or src/allmydata/_version.py,
# since 'setup.py test' always updates the version and builds before testing.
test:
	$(PYTHON) setup.py test $(TRIALARGS) -s $(TEST)
	touch .built

check: test

test-coverage: build
	rm -f .coverage
	$(TAHOE) debug trial --reporter=bwverbose-coverage $(TEST)

quicktest:
	$(TAHOE) debug trial $(TRIALARGS) $(TEST)

# code-coverage: install the "coverage" package from PyPI, do "make
# quicktest-coverage" to do a unit test run with coverage-gathering enabled,
# then use "make coverate-output-text" for a brief report, or "make
# coverage-output" for a pretty HTML report. Also see "make .coverage.el" and
# misc/coding_tools/coverage.el for emacs integration.

quicktest-coverage:
	rm -f .coverage
	PYTHONPATH=. $(TAHOE) debug trial --reporter=bwverbose-coverage $(TEST)
# on my laptop, "quicktest" takes 239s, "quicktest-coverage" takes 304s

# --include appeared in coverage-3.4
COVERAGE_OMIT=--include '$(CURDIR)/src/allmydata/*' --omit '$(CURDIR)/src/allmydata/test/*'
coverage-output:
	rm -rf coverage-html
	coverage html -i -d coverage-html $(COVERAGE_OMIT)
	cp .coverage coverage-html/coverage.data
	@echo "now point your browser at coverage-html/index.html"

.PHONY: upload-coverage .coverage.el pyflakes count-lines
.PHONY: check-memory check-memory-once check-speed check-grid
.PHONY: repl test-darcs-boringfile test-clean clean find-trailing-spaces

.coverage.el: .coverage
	$(PYTHON) misc/coding_tools/coverage2el.py

# 'upload-coverage' is meant to be run with an UPLOAD_TARGET=host:/dir setting
ifdef UPLOAD_TARGET

ifndef UPLOAD_HOST
$(error UPLOAD_HOST must be set when using UPLOAD_TARGET)
endif
ifndef COVERAGEDIR
$(error COVERAGEDIR must be set when using UPLOAD_TARGET)
endif

upload-coverage:
	rsync -a coverage-html/ $(UPLOAD_TARGET)
	ssh $(UPLOAD_HOST) make update-tahoe-coverage COVERAGEDIR=$(COVERAGEDIR)
else
upload-coverage:
	echo "this target is meant to be run with UPLOAD_TARGET=host:/path/"
	false
endif

code-checks: build version-and-path check-interfaces -find-trailing-spaces -check-umids pyflakes

version-and-path:
	$(TAHOE) --version-and-path

check-interfaces:
	$(TAHOE) @misc/coding_tools/check-interfaces.py 2>&1 |tee violations.txt
	@echo

pyflakes:
	$(PYTHON) -OOu `which pyflakes` $(SOURCES) |sort |uniq
	@echo

check-umids:
	$(PYTHON) misc/coding_tools/check-umids.py `find $(SOURCES) -name '*.py'`
	@echo

-check-umids:
	-$(PYTHON) misc/coding_tools/check-umids.py `find $(SOURCES) -name '*.py'`
	@echo

count-lines:
	@echo -n "files: "
	@find src -name '*.py' |grep -v /build/ |wc --lines
	@echo -n "lines: "
	@cat `find src -name '*.py' |grep -v /build/` |wc --lines
	@echo -n "TODO: "
	@grep TODO `find src -name '*.py' |grep -v /build/` | wc --lines

check-memory: .built
	rm -rf _test_memory
	$(TAHOE) @src/allmydata/test/check_memory.py upload
	$(TAHOE) @src/allmydata/test/check_memory.py upload-self
	$(TAHOE) @src/allmydata/test/check_memory.py upload-POST
	$(TAHOE) @src/allmydata/test/check_memory.py download
	$(TAHOE) @src/allmydata/test/check_memory.py download-GET
	$(TAHOE) @src/allmydata/test/check_memory.py download-GET-slow
	$(TAHOE) @src/allmydata/test/check_memory.py receive

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
check-grid: .built
	if [ -z '$(TESTCLIENTDIR)' ]; then exit 1; fi
	$(TAHOE) @src/allmydata/test/check_grid.py $(TESTCLIENTDIR) bin/tahoe

bench-dirnode: .built
	$(TAHOE) @src/allmydata/test/bench_dirnode.py

# 'make repl' is a simple-to-type command to get a Python interpreter loop
# from which you can type 'import allmydata'
repl:
	$(TAHOE) debug repl

test-darcs-boringfile:
	$(MAKE)
	$(PYTHON) misc/build_helpers/test-darcs-boringfile.py

test-clean:
	find . |grep -vEe "_darcs|allfiles.tmp|src/allmydata/_(version|appname).py" |sort >allfiles.tmp.old
	$(MAKE)
	$(MAKE) clean
	find . |grep -vEe "_darcs|allfiles.tmp|src/allmydata/_(version|appname).py" |sort >allfiles.tmp.new
	diff allfiles.tmp.old allfiles.tmp.new

# It would be nice if 'make clean' deleted any automatically-generated
# _version.py too, so that 'make clean; make all' could be useable as a
# "what the heck is going on, get me back to a clean state', but we need
# 'make clean' to work on non-darcs trees without destroying useful information.
clean:
	rm -rf build _trial_temp _test_memory .built
	rm -f `find src *.egg -name '*.so' -or -name '*.pyc'`
	rm -rf src/allmydata_tahoe.egg-info
	rm -rf support dist
	rm -rf `ls -d *.egg | grep -vEe"setuptools-|setuptools_darcs-|darcsver-"`
	rm -rf *.pyc
	rm -rf misc/dependencies/build misc/dependencies/temp
	rm -rf misc/dependencies/tahoe_deps.egg-info
	rm -f bin/tahoe bin/tahoe.pyscript

find-trailing-spaces:
	$(PYTHON) misc/coding_tools/find-trailing-spaces.py -r $(SOURCES)
	@echo

-find-trailing-spaces:
	-$(PYTHON) misc/coding_tools/find-trailing-spaces.py -r $(SOURCES)
	@echo

# The test-desert-island target grabs the tahoe-deps tarball, unpacks it,
# does a build, then asserts that the build did not try to download anything
# as it ran. Invoke this on a new tree, or after a 'clean', to make sure the
# support/lib/ directory is gone.

fetch-and-unpack-deps:
	test -f tahoe-deps.tar.gz || wget https://tahoe-lafs.org/source/tahoe/deps/tahoe-deps.tar.gz
	rm -rf tahoe-deps
	tar xzf tahoe-deps.tar.gz

test-desert-island:
	$(MAKE) fetch-and-unpack-deps
	$(MAKE) 2>&1 | tee make.out
	$(PYTHON) misc/build_helpers/check-build.py make.out no-downloads


# TARBALL GENERATION
.PHONY: tarballs upload-tarballs
tarballs:
	$(MAKE) make-version
	$(PYTHON) setup.py sdist --formats=bztar,gztar,zip
	$(PYTHON) setup.py sdist --sumo --formats=bztar,gztar,zip

upload-tarballs:
	@if [ "X${BB_BRANCH}" = "Xtrunk" ] || [ "X${BB_BRANCH}" = "X" ]; then for f in dist/allmydata-tahoe-*; do flappclient --furlfile ~/.tahoe-tarball-upload.furl upload-file $$f; done ; else echo not uploading tarballs because this is not trunk but is branch \"${BB_BRANCH}\" ; fi
