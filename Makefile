
# NOTE: this Makefile requires GNU make

default: build

PYTHON=python
export PYTHON

# setup.py will extend sys.path to include our support/lib/... directory
# itself. It will also create it in the beginning of the 'develop' command.

PP=$(shell $(PYTHON) setup.py -q show_pythonpath)
RUNPP=$(PYTHON) setup.py run_with_pythonpath

.PHONY: make-version build

# The 'darcsver' setup.py command comes in the 'darcsver' package:
# http://pypi.python.org/pypi/darcsver It is necessary only if you want to
# automatically produce a new _version.py file from the current darcs history.
make-version:
	$(PYTHON) ./setup.py darcsver --count-all-patches

# We want src/allmydata/_version.py to be up-to-date, but it's a fairly
# expensive operation (about 6 seconds on a just-before-0.7.0 tree, probably
# because of the 332 patches since the last tag), and we've removed the need
# for an explicit 'build' step by removing the C code from src/allmydata and
# by running everything in place. It would be neat to do:
#
#src/allmydata/_version.py: _darcs/patches
#	$(MAKE) make-version
#
# since that would update the embedded version string each time new darcs
# patches were pulled, but without an obligatory 'build' step this rule
# wouldn't be run frequently enough anyways.
#
# So instead, I'll just make sure that we update the version at least once
# when we first start using the tree, and again whenever an explicit
# 'make-version' is run, since then at least the developer has some means to
# update things. It would be nice if 'make clean' deleted any
# automatically-generated _version.py too, so that 'make clean; make all'
# could be useable as a "what the heck is going on, get me back to a clean
# state', but we need 'make clean' to work on non-darcs trees without
# destroying useful information.

.built:
	$(MAKE) build

src/allmydata/_version.py:
	$(MAKE) make-version

# c.f. ticket #455, there is a problem in the intersection of setuptools,
# twisted's setup.py, and nevow's setup.py . A Tahoe build, to satisfy its
# dependencies, may try to build both Twisted and Nevow. If both of these
# occur during the same invocation of 'setup.py develop', then the Nevow
# build will fail with an "ImportError: No module named components". Running
# the build a second time will succeed. Until there is a new version of
# setuptools which properly sandboxes sys.modules (or a new version of nevow
# which doesn't import twisted during its build, or a new version of twisted
# which doesn't import itself during its build), we just build tahoe twice
# and ignore the errors from the first pass. Updated 16-Sep-2008: now we need
# three invocations.

build: src/allmydata/_version.py
	$(PYTHON) setup.py build
	touch .built

# 'make install' will do the following:
#   build+install tahoe (probably to /usr/lib/pythonN.N/site-packages)
# 'make install PREFIX=/usr/local/stow/tahoe-N.N' will do the same, but to
# a different location

install: src/allmydata/_version.py
ifdef PREFIX
	mkdir -p $(PREFIX)
	$(PYTHON) ./setup.py install --single-version-externally-managed \
           --prefix=$(PREFIX) --record=./tahoe.files
else
	$(PYTHON) ./setup.py install --single-version-externally-managed
endif


# TESTING

.PHONY: signal-error-deps test test-coverage quicktest quicktest-coverage
.PHONY: coverage-output get-old-coverage-coverage coverage-delta-output


signal-error-deps:
	@echo
	@echo
	@echo "ERROR: Not all of Tahoe's dependencies are in place.  Please see docs/install.html for help on installing dependencies."
	@echo
	@echo
	exit 1

check-auto-deps:
	$(PYTHON) setup.py -q check_auto_deps || $(MAKE) signal-error-deps

.checked-deps:
	$(MAKE) check-auto-deps
	touch .checked-deps

# you can use 'make test TEST=allmydata.test.test_introducer' to run just
# test_introducer. TEST=allmydata.test.test_client.Basic.test_permute works
# too.
TEST=allmydata

# use 'make test TRIALARGS=--reporter=bwverbose' from buildbot, to
# suppress the ansi color sequences

test: build src/allmydata/_version.py
	$(PYTHON) setup.py test $(TRIALARGS) -s $(TEST)

fuse-test: .built .checked-deps
	$(RUNPP) -d contrib/fuse -p -c runtests.py

test-coverage: build src/allmydata/_version.py
	rm -f .coverage
	$(PYTHON) setup.py trial --reporter=bwverbose-coverage -s $(TEST)

quicktest:
	$(PYTHON) misc/build_helpers/run-with-pythonpath.py trial $(TRIALARGS) $(TEST)

# code-coverage: install the "coverage" package from PyPI, do "make
# quicktest-coverage" to do a unit test run with coverage-gathering enabled,
# then use "make coverate-output-text" for a brief report, or "make
# coverage-output" for a pretty HTML report. Also see "make .coverage.el" and
# misc/coding_helpers/coverage.el for emacs integration.

quicktest-coverage:
	rm -f .coverage
	$(PYTHON) misc/build_helpers/run-with-pythonpath.py trial --reporter=bwverbose-coverage $(TEST)
# on my laptop, "quicktest" takes 239s, "quicktest-coverage" takes 304s

coverage-output:
	rm -rf coverage-html
	coverage html -d coverage-html
	cp .coverage coverage-html/coverage.data
	@echo "now point your browser at coverage-html/index.html"

## use these two targets to compare this coverage against the previous run.
## The deltas only work if the old test was run in the same directory, since
## it compares absolute filenames.
#get-old-figleaf-coverage:
#	wget --progress=dot -O old.figleaf http://allmydata.org/tahoe-figleaf/current/figleaf.pickle
#
#figleaf-delta-output:
#	$(RUNPP) -p -c "misc/figleaf2html -d coverage-html -r src -x misc/figleaf.excludes -o old.figleaf"
#	cp .figleaf coverage-html/figleaf.pickle
#	@echo "now point your browser at coverage-html/index.html"

.PHONY: upload-coverage .coverage.el pyflakes count-lines
.PHONY: check-memory check-memory-once check-speed check-grid
.PHONY: repl test-darcs-boringfile test-clean clean find-trailing-spaces

.coverage.el: .coverage
	$(PYTHON) misc/coding_helpers/coverage2el.py

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


pyflakes:
	$(PYTHON) -OOu `which pyflakes` src/allmydata |sort |uniq

count-lines:
	@echo -n "files: "
	@find src -name '*.py' |grep -v /build/ |wc --lines
	@echo -n "lines: "
	@cat `find src -name '*.py' |grep -v /build/` |wc --lines
	@echo -n "TODO: "
	@grep TODO `find src -name '*.py' |grep -v /build/` | wc --lines

check-memory: .built
	rm -rf _test_memory
	$(RUNPP) -p -c "src/allmydata/test/check_memory.py upload"
	$(RUNPP) -p -c "src/allmydata/test/check_memory.py upload-self"
	$(RUNPP) -p -c "src/allmydata/test/check_memory.py upload-POST"
	$(RUNPP) -p -c "src/allmydata/test/check_memory.py download"
	$(RUNPP) -p -c "src/allmydata/test/check_memory.py download-GET"
	$(RUNPP) -p -c "src/allmydata/test/check_memory.py download-GET-slow"
	$(RUNPP) -p -c "src/allmydata/test/check_memory.py receive"

check-memory-once: .built
	rm -rf _test_memory
	$(RUNPP) -p -c "src/allmydata/test/check_memory.py $(MODE)"

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
	-$(PYTHON) bin/tahoe stop $(TESTCLIENTDIR)
	$(PYTHON) bin/tahoe start $(TESTCLIENTDIR)
	sleep 5
	$(PYTHON) src/allmydata/test/check_speed.py $(TESTCLIENTDIR)
	$(PYTHON) bin/tahoe stop $(TESTCLIENTDIR)

# The check-grid target also uses a pre-established client node, along with a
# long-term directory that contains some well-known files. See the docstring
# in src/allmydata/test/check_grid.py to see how to set this up.
check-grid: .built
	if [ -z '$(TESTCLIENTDIR)' ]; then exit 1; fi
	$(PYTHON) src/allmydata/test/check_grid.py $(TESTCLIENTDIR) bin/tahoe

bench-dirnode: .built
	$(RUNPP) -p -c src/allmydata/test/bench_dirnode.py

# 'make repl' is a simple-to-type command to get a Python interpreter loop
# from which you can type 'import allmydata'
repl:
	$(RUNPP) -p

test-darcs-boringfile:
	$(MAKE)
	$(PYTHON) misc/build_helpers/test-darcs-boringfile.py

test-clean:
	find . |grep -vEe "_darcs|allfiles.tmp|src/allmydata/_(version|auto_deps|appname).py" |sort >allfiles.tmp.old
	$(MAKE)
	$(MAKE) clean
	find . |grep -vEe "_darcs|allfiles.tmp|src/allmydata/_(version|auto_deps|appname).py" |sort >allfiles.tmp.new
	diff allfiles.tmp.old allfiles.tmp.new

clean:
	rm -rf build _trial_temp _test_memory .checked-deps .built
	rm -f `find src *.egg -name '*.so' -or -name '*.pyc'`
	rm -rf src/allmydata_tahoe.egg-info
	rm -rf support dist
	rm -rf `ls -d *.egg | grep -vEe"setuptools-|setuptools_trial-|darcsver-"`
	rm -rf *.pyc
	rm -rf misc/dependencies/build misc/dependencies/temp
	rm -rf misc/dependencies/tahoe_deps.egg-info
	rm -f bin/tahoe bin/tahoe-script.py

find-trailing-spaces:
	$(PYTHON) misc/coding_tools/find-trailing-spaces.py -r src

# The test-desert-island target grabs the tahoe-deps tarball, unpacks it,
# does a build, then asserts that the build did not try to download anything
# as it ran. Invoke this on a new tree, or after a 'clean', to make sure the
# support/lib/ directory is gone.

fetch-and-unpack-deps:
	test -f tahoe-deps.tar.gz || wget http://allmydata.org/source/tahoe/deps/tahoe-deps.tar.gz
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
	@if [ "X${BB_BRANCH}" == "Xtrunk" ]; then for f in dist/allmydata-tahoe-*; do flappclient --furlfile ~/.tahoe-tarball-upload.furl upload-file $$f; done ; else echo not uploading tarballs because this is not trunk but is branch \"${BB_BRANCH}\" ; fi

# DEBIAN PACKAGING

VER=$(shell $(PYTHON) misc/build_helpers/get-version.py)
DEBCOMMENTS="'make deb' build"

show-version:
	@echo $(VER)
show-pp:
	@echo $(PP)

.PHONY: setup-deb deb-ARCH is-known-debian-arch
.PHONY: deb-etch deb-lenny deb-sid
.PHONY: deb-edgy deb-feisty deb-gutsy deb-hardy deb-intrepid deb-jaunty

# we use misc/debian_helpers/$TAHOE_ARCH/debian

deb-etch:      # py2.4
	$(MAKE) deb-ARCH ARCH=etch TAHOE_ARCH=etch
deb-lenny:     # py2.5
	$(MAKE) deb-ARCH ARCH=lenny TAHOE_ARCH=lenny
deb-sid:
	$(MAKE) deb-ARCH ARCH=sid TAHOE_ARCH=sid

deb-edgy:     # py2.4
	$(MAKE) deb-ARCH ARCH=edgy TAHOE_ARCH=etch
deb-feisty:   # py2.5
	$(MAKE) deb-ARCH ARCH=feisty TAHOE_ARCH=lenny
deb-gutsy:    # py2.5
	$(MAKE) deb-ARCH ARCH=gutsy TAHOE_ARCH=lenny
deb-hardy:    # py2.5
	$(MAKE) deb-ARCH ARCH=hardy TAHOE_ARCH=lenny
deb-intrepid: # py2.5
	$(MAKE) deb-ARCH ARCH=intrepid TAHOE_ARCH=lenny
deb-jaunty:   # py2.6
	$(MAKE) deb-ARCH ARCH=jaunty TAHOE_ARCH=lenny



# we know how to handle the following debian architectures
KNOWN_DEBIAN_ARCHES := etch lenny sid  edgy feisty gutsy hardy intrepid jaunty

ifeq ($(findstring x-$(ARCH)-x,$(foreach arch,$(KNOWN_DEBIAN_ARCHES),"x-$(arch)-x")),)
is-known-debian-arch:
	@echo "ARCH must be set when using setup-deb or deb-ARCH"
	@echo "I know how to handle:" $(KNOWN_DEBIAN_ARCHES)
	false
else
is-known-debian-arch:
	true
endif

ifndef TAHOE_ARCH
TAHOE_ARCH=$(ARCH)
endif

setup-deb: is-known-debian-arch
	rm -f debian
	ln -s misc/debian_helpers/$(TAHOE_ARCH)/debian debian
	chmod +x debian/rules

# etch (current debian stable) has python-simplejson-1.3, which doesn't
#  support indent=
# sid (debian unstable) currently has python-simplejson 1.7.1
# edgy has 1.3, which doesn't support indent=
# feisty has 1.4, which supports indent= but emits a deprecation warning
# gutsy has 1.7.1
#
# we need 1.4 or newer

deb-ARCH: is-known-debian-arch setup-deb
	fakeroot debian/rules binary
	@echo
	@echo "The newly built .deb packages are in the parent directory from here."

.PHONY: increment-deb-version
.PHONY: deb-etch-head deb-lenny-head deb-sid-head
.PHONY: deb-edgy-head deb-feisty-head deb-gutsy-head deb-hardy-head deb-intrepid-head deb-jaunty-head

# The buildbot runs the following targets after each change, to produce
# up-to-date tahoe .debs. These steps do not create .debs for anything else.

increment-deb-version: make-version
	debchange --newversion $(VER) $(DEBCOMMENTS)
deb-etch-head:
	$(MAKE) setup-deb ARCH=etch TAHOE_ARCH=etch
	$(MAKE) increment-deb-version
	fakeroot debian/rules binary
deb-lenny-head:
	$(MAKE) setup-deb ARCH=lenny TAHOE_ARCH=lenny
	$(MAKE) increment-deb-version
	fakeroot debian/rules binary
deb-sid-head:
	$(MAKE) setup-deb ARCH=sid TAHOE_ARCH=lenny
	$(MAKE) increment-deb-version
	fakeroot debian/rules binary

deb-edgy-head:
	$(MAKE) setup-deb ARCH=edgy TAHOE_ARCH=etch
	$(MAKE) increment-deb-version
	fakeroot debian/rules binary
deb-feisty-head:
	$(MAKE) setup-deb ARCH=feisty TAHOE_ARCH=lenny
	$(MAKE) increment-deb-version
	fakeroot debian/rules binary
deb-gutsy-head:
	$(MAKE) setup-deb ARCH=gutsy TAHOE_ARCH=lenny
	$(MAKE) increment-deb-version
	fakeroot debian/rules binary
deb-hardy-head:
	$(MAKE) setup-deb ARCH=hardy TAHOE_ARCH=lenny
	$(MAKE) increment-deb-version
	fakeroot debian/rules binary
deb-intrepid-head:
	$(MAKE) setup-deb ARCH=intrepid TAHOE_ARCH=lenny
	$(MAKE) increment-deb-version
	fakeroot debian/rules binary
deb-jaunty-head:
	$(MAKE) setup-deb ARCH=jaunty TAHOE_ARCH=lenny
	$(MAKE) increment-deb-version
	fakeroot debian/rules binary

# new experimental debian-packaging-building target
.PHONY: EXPERIMENTAL-deb
EXPERIMENTAL-deb: is-known-debian-arch
	$(PYTHON) misc/build_helpers/build-deb.py $(ARCH)


# These targets provide for windows native builds
.PHONY: windows-exe windows-installer windows-installer-upload

windows-exe: .built
	$(RUNPP) -c "$(MAKE) -C windows windows-exe"

windows-installer:
	$(RUNPP) -c "$(MAKE) -C windows windows-installer"

windows-installer-upload:
	$(RUNPP) -c "$(MAKE) -C windows windows-installer-upload"


# These targets provide for mac native builds
.PHONY: mac-exe mac-upload mac-cleanup mac-dbg

mac-exe: .built
	$(MAKE) -C mac clean
	VERSION=$(VER) $(RUNPP) -c "$(MAKE) -C mac build"

mac-dist:
	VERSION=$(VER) $(MAKE) -C mac diskimage

mac-upload:
	VERSION=$(VER) $(MAKE) -C mac upload

mac-cleanup:
	VERSION=$(VER) $(MAKE) -C mac cleanup

mac-dbg:
	cd mac && $(PP) $(PYTHON)w allmydata_tahoe.py

