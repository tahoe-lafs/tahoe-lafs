
# this Makefile requires GNU make

# If you get an error message like the following:

# error: Setup script exited with error: Python was built with version 7.1 of Visual Studio, and extensions need to be built with the same version of the compiler, but it isn't installed.

# Then that probably means that you aren't using the the right
# compiler.  In that case, try creating distutils configuration file
# (as described in http://docs.python.org/inst/config-syntax.html ),
# specifying which compiler to use.  For example, if you use either
# the cygwin gcc compiler with mingw support, or the MINGW compiler,
# then you can add the following lines to your .cfg file:
# [build]
# compiler=mingw32

default: build

PYTHON=python
PATHSEP=$(shell python -c 'import os ; print os.pathsep')
TRIALPATH=$(shell which trial.py 2>/dev/null)
ifeq ($(TRIALPATH),)
TRIALPATH=$(shell which trial 2>/dev/null)
endif
ifeq ($(TRIALPATH),)
TRIALPATH=$(shell $(PYTHON) -c "import os, sys; print repr(os.path.join(sys.prefix, \"Scripts\", \"trial.py\"))")
endif
ifeq ($(TRIALPATH),)
TRIALPATH=$(shell $(PYTHON) -c "import os, sys; print repr(os.path.join(sys.prefix, \"Scripts\", \"trial\"))")
endif

REACTOR=

PLAT = $(strip $(shell python -c "import sys ; print sys.platform"))
ifeq ($(PLAT),win32)
 # The platform is Windows with cygwin build tools and the native Python interpreter.
 TRIALPATH := $(shell cygpath -w $(TRIALPATH))
 SUPPORT = $(shell cygpath -w $(shell pwd))\support
 SUPPORTLIB := $(SUPPORT)\Lib\site-packages
else
 PYVER=$(shell $(PYTHON) misc/pyver.py)
 SUPPORT = $(shell pwd)/support
 SUPPORTLIB = $(SUPPORT)/lib/$(PYVER)/site-packages
endif

ifeq ($(PLAT),cygwin)
REACTOR = poll
endif

ifneq ($(REACTOR),)
	REACTOROPT := --reactor=$(REACTOR)
else
	REACTOROPT := 
endif

TRIAL=$(PYTHON) -u "$(TRIALPATH)" --rterrors $(REACTOROPT)

build-deps:
	mkdir -p "$(SUPPORTLIB)"
	PYTHONPATH="$(PYTHONPATH)$(PATHSEP)$(SUPPORTLIB)" $(PYTHON) setup.py install \
	 --prefix="$(SUPPORT)"

EGGSPATH = $(shell $(PYTHON) misc/find-dep-eggs.py)
show-eggspath:
	@echo $(EGGSPATH)

PP=PYTHONPATH="src$(PATHSEP)$(EGGSPATH)$(PATHSEP)$(PYTHONPATH)"

.PHONY: make-version build
make-version:
	$(PYTHON) misc/make-version.py "allmydata-tahoe" "src/allmydata/_version.py"

build: make-version
	$(PYTHON) ./setup.py build_ext -i

# 'make install' will do the following:
#   build+install tahoe (probably to /usr/lib/pythonN.N/site-packages)

# 'make install PREFIX=/usr/local/stow/tahoe-N.N' will do the following:
#   build+install tahoe to the given PREFIX

install: make-version
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

.PHONY: test test-figleaf figleaf-output

# you can use 'make test TEST=allmydata.test.test_introducer' to run just
# test_introducer. TEST=allmydata.test.test_client.Basic.test_permute works
# too.
TEST=allmydata

# use 'make test REPORTER=--reporter=bwverbose' from buildbot, to
# suppress the ansi color sequences

test: build
	$(PP) $(PYTHON) -c 'import allmydata, zfec, foolscap, simplejson, nevow, OpenSSL'
	$(PP) $(TRIAL) $(REPORTER) $(TEST)

test-figleaf: build
	rm -f .figleaf
	$(PP) $(TRIAL) --reporter=bwverbose-figleaf $(TEST)

figleaf-output:
	$(PP) $(PYTHON) misc/figleaf2html -d coverage-html -r src -x misc/figleaf.excludes
	@echo "now point your browser at coverage-html/index.html"

# after doing test-figleaf and figleaf-output, point your browser at
# coverage-html/index.html

.PHONY: upload-figleaf .figleaf.el pyflakes count-lines check-memory
.PHONY: clean

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

check-memory: build
	touch memstats.out
	$(PP) $(PYTHON) src/allmydata/test/check_memory.py upload
	cat _test_memory/stats.out >>memstats.out
	$(PP) $(PYTHON) src/allmydata/test/check_memory.py upload-self
	cat _test_memory/stats.out >>memstats.out
	$(PP) $(PYTHON) src/allmydata/test/check_memory.py upload-POST
	cat _test_memory/stats.out >>memstats.out

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
	rm -rf build
	rm -f debian
	rm -f `find src/allmydata -name '*.so' -or -name '*.pyc'`
	rm -rf allmydata_tahoe.egg-info
	rm -rf support
	rm -rf setuptools*.egg
	rm -f ez_setup.pyc



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
	echo
	echo "The newly built .deb packages are in the parent directory from here."

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
