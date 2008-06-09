
# NOTE: this Makefile requires GNU make

default: build

PYTHON=python
PATHSEP=$(shell python -c 'import os ; print os.pathsep')
OSSEP=$(shell python -c 'import os ; print os.sep')

REACTOR=

ifneq ($(INCLUDE_DIRS),)
INCLUDE_DIRS_ARG = -I$(INCLUDE_DIRS)
endif
ifneq ($(LIBRARY_DIRS),)
LIBRARY_DIRS_ARG = -L$(LIBRARY_DIRS)
endif

PLAT = $(strip $(shell python -c "import sys ; print sys.platform"))
ifeq ($(PLAT),win32)
	# The platform is Windows with cygwin build tools and the native Python interpreter.
	SUPPORT = $(shell cygpath -w $(shell pwd))\support
	SUPPORTLIB := $(SUPPORT)\Lib\site-packages
	SRCPATH := $(shell cygpath -w $(shell pwd)/src)
	INNOSETUP := $(shell cygpath -au "$(PROGRAMFILES)/Inno Setup 5/Compil32.exe")
else
	PYVER=$(shell $(PYTHON) misc/pyver.py)
	SUPPORT = $(shell pwd)/support
	SUPPORTLIB = $(SUPPORT)/lib/$(PYVER)/site-packages
	SRCPATH := $(shell pwd)/src
	CHECK_PYWIN32_DEP := 
endif

ifeq ($(PLAT),cygwin)
REACTOR = poll
endif

ifneq ($(REACTOR),)
	REACTOROPT := --reactor=$(REACTOR)
else
	REACTOROPT := 
endif

ifneq ($(PYTHONPATH),)
        PP=PYTHONPATH="$(PYTHONPATH)$(PATHSEP)$(SUPPORTLIB)"
else
        PP=PYTHONPATH="$(SUPPORTLIB)"
endif

TRIALCMD = $(shell PATH="${PATH}:${PWD}/support/bin" $(PP) $(PYTHON) misc/find_trial.py)
TRIAL=PATH="${PATH}:${PWD}/support/bin" PYTHONUNBUFFERED=1 $(TRIALCMD) --rterrors $(REACTOROPT)

.PHONY: make-version build

# The 'darcsver' setup.py command comes in the 'darcsver' package:
# http://pypi.python.org/pypi/darcsver It is necessary only if you want to
# automatically produce a new _version.py file from the current darcs history.
make-version:
	$(PP) $(PYTHON) ./setup.py darcsver --count-all-patches

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
# patches were pulled, but 1) this would break non-darcs trees (i.e. building
# from an exported tarball), and 2) without an obligatory 'build' step this
# rule wouldn't be run frequently enought anyways.
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
# and ignore the errors from the first pass.

build: src/allmydata/_version.py
	-$(MAKE) build-once
	$(MAKE) build-once

build-once:
	mkdir -p "$(SUPPORTLIB)"
	$(PP) $(PYTHON) ./setup.py develop --prefix="$(SUPPORT)"
	chmod +x bin/tahoe
	touch .built

# 'make install' will do the following:
#   build+install tahoe (probably to /usr/lib/pythonN.N/site-packages)
# 'make install PREFIX=/usr/local/stow/tahoe-N.N' will do the same, but to
# a different location

install: src/allmydata/_version.py
ifdef PREFIX
	mkdir -p $(PREFIX)
	$(PP) $(PYTHON) ./setup.py install \
           --single-version-externally-managed \
           --prefix=$(PREFIX) --record=./tahoe.files
else
	$(PP) $(PYTHON) ./setup.py install \
           --single-version-externally-managed
endif


# TESTING

.PHONY: signal-error-deps test test-figleaf figleaf-output


signal-error-deps:
	@echo
	@echo
	@echo "ERROR: Not all of Tahoe's dependencies are in place.  Please see docs/install.html for help on installing dependencies."
	@echo
	@echo
	exit 1

check-auto-deps:
	@$(PP) $(PYTHON) -c 'import _auto_deps ; _auto_deps.require_auto_deps()' || $(MAKE) signal-error-deps

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
	$(PP) $(TRIAL) $(TRIALARGS) $(TEST)

quicktest: .built .checked-deps
	$(PP) $(TRIAL) $(TRIALARGS) $(TEST)

test-figleaf: build src/allmydata/_version.py
	rm -f .figleaf
	$(PP) $(TRIAL) --reporter=bwverbose-figleaf $(TEST)

quicktest-figleaf: src/allmydata/_version.py
	rm -f .figleaf
	$(PP) $(TRIAL) --reporter=bwverbose-figleaf $(TEST)

figleaf-output:
	$(PP) \
	 $(PYTHON) misc/figleaf2html -d coverage-html -r src -x misc/figleaf.excludes
	@echo "now point your browser at coverage-html/index.html"

# after doing test-figleaf and figleaf-output, point your browser at
# coverage-html/index.html

.PHONY: upload-figleaf .figleaf.el pyflakes count-lines
.PHONY: check-memory check-memory-once clean

# 'upload-figleaf' is meant to be run with an UPLOAD_TARGET=host:/dir setting
ifdef UPLOAD_TARGET

ifndef UPLOAD_HOST
$(error UPLOAD_HOST must be set when using UPLOAD_TARGET)
endif
ifndef COVERAGEDIR
$(error COVERAGEDIR must be set when using UPLOAD_TARGET)
endif

upload-figleaf:
	rsync -a coverage-html/ $(UPLOAD_TARGET)
	ssh $(UPLOAD_HOST) make update-tahoe-figleaf COVERAGEDIR=$(COVERAGEDIR)
else
upload-figleaf:
	echo "this target is meant to be run with UPLOAD_TARGET=host:/path/"
	false
endif

.figleaf.el: .figleaf
	$(PP) $(PYTHON) misc/figleaf2el.py .figleaf src

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
	$(PP) \
	 $(PYTHON) src/allmydata/test/check_memory.py upload
	$(PP) \
	 $(PYTHON) src/allmydata/test/check_memory.py upload-self
	$(PP) \
	 $(PYTHON) src/allmydata/test/check_memory.py upload-POST
	$(PP) \
	 $(PYTHON) src/allmydata/test/check_memory.py download
	$(PP) \
	 $(PYTHON) src/allmydata/test/check_memory.py download-GET
	$(PP) \
	 $(PYTHON) src/allmydata/test/check_memory.py download-GET-slow
	$(PP) \
	 $(PYTHON) src/allmydata/test/check_memory.py receive

check-memory-once: .built
	rm -rf _test_memory
	$(PP) \
	 $(PYTHON) src/allmydata/test/check_memory.py $(MODE)

# this target uses a pre-established client node to run a canned set of
# performance tests against a test network that is also pre-established
# (probably on a remote machine). Provide it with the path to a local
# directory where this client node has been created (and populated with the
# necessary FURLs of the test network). This target will start that client
# with the current code and then run the tests. Afterwards it will stop the
# client.
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

# 'make repl' is a simple-to-type command to get a Python interpreter loop
# from which you can type 'import allmydata'
repl:
	$(PP) python

test-darcs-boringfile:
	$(MAKE)
	$(PYTHON) misc/test-darcs-boringfile.py

test-clean:
	find . |grep -vEe"allfiles.tmp|src/allmydata/_(version|auto_deps).py|src/allmydata_tahoe.egg-info" |sort >allfiles.tmp.old
	$(MAKE)
	$(MAKE) clean
	find . |grep -vEe"allfiles.tmp|src/allmydata/_(version|auto_deps).py|src/allmydata_tahoe.egg-info" |sort >allfiles.tmp.new
	diff allfiles.tmp.old allfiles.tmp.new

clean:
	rm -rf build _trial_temp _test_memory .checked-deps .built
	rm -f debian
	rm -f `find src/allmydata -name '*.so' -or -name '*.pyc'`
	rm -rf tahoe_deps.egg-info allmydata_tahoe.egg-info
	rm -rf support dist
	rm -rf setuptools*.egg *.pyc darcsver*.egg pyutil*.egg
	rm -rf misc/dependencies/build misc/dependencies/temp
	rm -rf misc/dependencies/tahoe_deps.egg-info

find-trailing-spaces:
	$(PYTHON) misc/find-trailing-spaces.py -r src

# TARBALL GENERATION
.PHONY: tarballs upload-tarballs
tarballs:
	$(MAKE) make-version
	$(PYTHON) setup.py sdist --formats=bztar,gztar,zip
upload-tarballs:
	for f in dist/allmydata-tahoe-*; do \
	 xfer-client --furlfile ~/.tahoe-tarball-upload.furl $$f; \
	done

# DEBIAN PACKAGING

VER=$(shell $(PYTHON) misc/get-version.py)
DEBCOMMENTS="'make deb' build"

show-version:
	@echo $(VER)

.PHONY: setup-deb deb-ARCH is-known-debian-arch
.PHONY: deb-sid deb-feisty deb-edgy deb-etch

deb-sid:
	$(MAKE) deb-ARCH ARCH=sid
deb-feisty:
	$(MAKE) deb-ARCH ARCH=feisty
# edgy uses the feisty control files for now
deb-edgy:
	$(MAKE) deb-ARCH ARCH=edgy TAHOE_ARCH=feisty
# etch uses the feisty control files for now
deb-etch:
	$(MAKE) deb-ARCH ARCH=etch TAHOE_ARCH=feisty
# same with gutsy, the process has been nicely stable for a while now
deb-gutsy:
	$(MAKE) deb-ARCH ARCH=gutsy TAHOE_ARCH=feisty

# we know how to handle the following debian architectures
KNOWN_DEBIAN_ARCHES := sid feisty edgy etch gutsy

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
	ln -s misc/$(TAHOE_ARCH)/debian debian
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
.PHONY: deb-sid-head deb-edgy-head deb-feisty-head
.PHONY: deb-etch-head

# The buildbot runs the following targets after each change, to produce
# up-to-date tahoe .debs. These steps do not create .debs for anything else.

increment-deb-version: make-version
	debchange --newversion $(VER) $(DEBCOMMENTS)
deb-sid-head:
	$(MAKE) setup-deb ARCH=sid
	$(MAKE) increment-deb-version
	fakeroot debian/rules binary
deb-edgy-head:
	$(MAKE) setup-deb ARCH=edgy TAHOE_ARCH=feisty
	$(MAKE) increment-deb-version
	fakeroot debian/rules binary
deb-feisty-head:
	$(MAKE) setup-deb ARCH=feisty
	$(MAKE) increment-deb-version
	fakeroot debian/rules binary
deb-etch-head:
	$(MAKE) setup-deb ARCH=etch TAHOE_ARCH=feisty
	$(MAKE) increment-deb-version
	fakeroot debian/rules binary
deb-gutsy-head:
	$(MAKE) setup-deb ARCH=gutsy TAHOE_ARCH=feisty
	$(MAKE) increment-deb-version
	fakeroot debian/rules binary

# These targets provide for windows native builds
.PHONY: windows-exe windows-installer windows-installer-upload

windows-exe: .built
	cd windows && $(PP) $(PYTHON) setup.py py2exe

windows-installer: windows-exe
	$(PP) $(PYTHON) misc/sub-ver.py windows/installer.tmpl >windows/installer.iss
	cd windows && "$(INNOSETUP)" /cc installer.iss

windows-installer-upload:
	chmod -R o+rx windows/dist/installer
	rsync -av -e /usr/bin/ssh windows/dist/installer/ amduser@dev:/home/amduser/public_html/dist/tahoe/windows/

# These targets provide for mac native builds
.PHONY: mac-exe mac-upload mac-cleanup mac-dbg

mac-exe: .built
	$(MAKE) -C mac clean
	VERSION=$(VER) $(PP) $(MAKE) -C mac build

mac-dist:
	VERSION=$(VER) $(MAKE) -C mac diskimage

mac-upload:
	VERSION=$(VER) $(MAKE) -C mac upload UPLOAD_DEST=$(UPLOAD_DEST)

mac-cleanup:
	VERSION=$(VER) $(MAKE) -C mac cleanup

mac-dbg:
	cd mac && $(PP) $(PYTHON)w allmydata_tahoe.py

# This target runs a stats gatherer server
.PHONY: stats-gatherer-run
stats-gatherer-run:
	cd stats_gatherer && $(PP) $(PYTHON) ../src/allmydata/stats.py
