
# this Makefile requires GNU make

default: build

PYTHON=python
PATHSEP=$(shell python -c 'import os ; print os.pathsep')
OSSEP=$(shell python -c 'import os ; print os.sep')

REACTOR=

PLAT = $(strip $(shell python -c "import sys ; print sys.platform"))
ifeq ($(PLAT),win32)
 # The platform is Windows with cygwin build tools and the native Python interpreter.
 SUPPORT = $(shell cygpath -w $(shell pwd))\support
 SUPPORTLIB := $(SUPPORT)\Lib\site-packages
 SRCPATH := $(shell cygpath -w $(shell pwd)/src)
 CHECK_PYWIN32_DEP := check-pywin32-dep
else
 PYVER=$(shell $(PYTHON) misc/pyver.py)
 SUPPORT = $(shell pwd)/support
 SUPPORTLIB = $(SUPPORT)/lib/$(PYVER)/site-packages
 SRCPATH := $(shell pwd)/src
 CHECK_PYWIN32_DEP := 
endif

TRIALCMD = $(shell PYTHONPATH="$(PYTHONPATH)$(PATHSEP)$(SRCPATH)" $(PYTHON) misc/find_trial.py)

ifeq ($(PLAT),cygwin)
REACTOR = poll
endif

ifneq ($(REACTOR),)
	REACTOROPT := --reactor=$(REACTOR)
else
	REACTOROPT := 
endif

TRIAL=PYTHONUNBUFFERED=1 $(TRIALCMD) --rterrors $(REACTOROPT)

# build-deps wants setuptools to have been built first. It's easiest to
# accomplish this by depending upon the tahoe compile.
build-deps: .built check-twisted-dep
	mkdir -p "$(SUPPORTLIB)"
	PYTHONPATH="$(PYTHONPATH)$(PATHSEP)$(SUPPORTLIB)$(PATHSEP)" \
         $(PYTHON) misc/dependencies/build-deps-setup.py install \
	 --prefix="$(SUPPORT)"

EGGSPATH = $(shell $(PYTHON) misc/find-dep-eggs.py)
show-eggspath:
	@echo $(EGGSPATH)

ifneq ($(PYTHONPATH),)
	PYTHONPATH := $(PYTHONPATH)$(PATHSEP)
endif
PP=PYTHONPATH="$(SRCPATH)$(PATHSEP)$(EGGSPATH)$(PATHSEP)$(PYTHONPATH)"

.PHONY: make-version build
# N.B.: the first argument to make-version.py is used to find darcs tags that
# represent released versions, so it needs to match whatever release
# conventions are in use.
make-version:
	$(PYTHON) misc/make-version.py "allmydata-tahoe" "src/allmydata/_version.py"

.built:
	$(MAKE) build
	touch .built

build: 
	$(PYTHON) ./setup.py build_ext -i
	chmod +x bin/tahoe

# 'make install' will do the following:
#   build+install tahoe (probably to /usr/lib/pythonN.N/site-packages)
# 'make install PREFIX=/usr/local/stow/tahoe-N.N' will do the same, but to
# a different location

install: 
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

.PHONY: check-deps check-twisted-dep $(CHECK_PYWIN32_DEP) signal-error-deps, signal-error-twisted-dep, signal-error-pywin32-dep, test test-figleaf figleaf-output


signal-error-deps:
	@echo "ERROR: Not all of Tahoe's dependencies are in place.  Please\
see the README for help on installing dependencies."
	exit 1

signal-error-twisted-dep:
	@echo "ERROR: Before running \"make build-deps\" you have to ensure that\
Twisted is installed (including its zope.interface dependency).  Twisted and\
zope.interface are required for the automatic installation of certain other\
libraries that Tahoe requires).  Please see the README for details."
	exit 1

signal-error-pywin32-dep:
	@echo "ERROR: the pywin32 dependency is not in place.  Please see the README\
for help on installing dependencies."
	exit 1

check-deps: check-twisted-dep $(CHECK_PYWIN32_DEP)
	$(PP) \
	 $(PYTHON) -c 'import allmydata, zfec, foolscap, simplejson, nevow, OpenSSL' || $(MAKE) signal-error-deps

check-twisted-dep:
	$(PYTHON) -c 'import twisted, zope.interface' || $(MAKE) signal-error-twisted-dep

check-pywin32-dep:
	$(PYTHON) -c 'import win32process' || $(MAKE) signal-error-pywin32-dep

.checked-deps:
	$(MAKE) check-deps
	touch .checked-deps

# you can use 'make test TEST=allmydata.test.test_introducer' to run just
# test_introducer. TEST=allmydata.test.test_client.Basic.test_permute works
# too.
TEST=allmydata

# use 'make test REPORTER=--reporter=bwverbose' from buildbot, to
# suppress the ansi color sequences

test: .built .checked-deps
	$(PP) \
	 $(TRIAL) $(REPORTER) $(TEST)

test-figleaf: .built .checked-deps
	rm -f .figleaf
	$(PP) \
	 $(TRIAL) --reporter=bwverbose-figleaf $(TEST)

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
	/bin/false
endif

.figleaf.el: .figleaf
	$(PP) \
	 $(PYTHON) misc/figleaf2el.py .figleaf src

pyflakes:
	$(PYTHON) -OOu `which pyflakes` src/allmydata

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
	$(PYTHON) bin/tahoe start $(TESTCLIENTDIR)
	sleep 5
	$(PYTHON) src/allmydata/test/check_speed.py $(TESTCLIENTDIR)
	$(PYTHON) bin/tahoe stop $(TESTCLIENTDIR)

test-darcs-boringfile:
	$(MAKE)
	$(PYTHON) misc/test-darcs-boringfile.py

test-clean:
	find . |grep -v allfiles.tmp |grep -v src/allmydata/_version.py |sort >allfiles.tmp.old
	$(MAKE)
	$(MAKE) clean
	find . |grep -v allfiles.tmp |grep -v src/allmydata/_version.py |sort >allfiles.tmp.new
	diff allfiles.tmp.old allfiles.tmp.new

clean:
	rm -rf build _trial_temp _test_memory .checked-deps .built
	rm -f debian
	rm -f `find src/allmydata -name '*.so' -or -name '*.pyc'`
	rm -rf tahoe_deps.egg-info allmydata_tahoe.egg-info
	rm -rf support dist
	rm -rf setuptools*.egg *.pyc



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

# we know how to handle the following debian architectures
KNOWN_DEBIAN_ARCHES := sid feisty edgy etch

ifeq ($(findstring x-$(ARCH)-x,$(foreach arch,$(KNOWN_DEBIAN_ARCHES),"x-$(arch)-x")),)
is-known-debian-arch:
	@echo "ARCH must be set when using setup-deb or deb-ARCH"
	@echo "I know how to handle:" $(KNOWN_DEBIAN_ARCHES)
	/bin/false
else
is-known-debian-arch:
	/bin/true
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
