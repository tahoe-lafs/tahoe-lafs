default: build

BASE=$(shell pwd)
PYTHON=python
INSTDIR=$(BASE)/instdir
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

EXTRA_SETUP_ARGS=
REACTOR=poll

PLAT = $(strip $(shell python -c "import sys ; print sys.platform"))
ifeq ($(PLAT),cygwin)
 # The platform is Windows with cygwin build tools and the cygwin Python interpreter.
 INSTDIR := $(shell cygpath -u $(INSTDIR))
else
 ifeq ($(PLAT),win32)
  # The platform is Windows with cygwin build tools and the native Python interpreter.
  EXTRA_SETUP_ARGS=build -c mingw32
  REACTOR=select
  INSTDIR := $(shell cygpath -w $(INSTDIR))
  TRIALPATH := $(shell cygpath -w $(TRIALPATH))
  ifneq ($(PYTHONPATH),)
   PYTHONPATH := $(shell cygpath -w $(PYTHONPATH))
  endif
 endif
endif

ORIGPYTHONPATH=$(PYTHONPATH)

# Append instdir/lib instead of prepending it so that people can override
# things from lib with alternate packages of their choosing by setting their
# PYTHONPATH.

ifneq ($(PYTHONPATH),)
PYTHONPATH := "$(PYTHONPATH)$(PATHSEP)$(INSTDIR)/lib"
else
PYTHONPATH := "$(INSTDIR)/lib"
endif

TRIAL=$(PYTHON) -u "$(TRIALPATH)" --rterrors --reactor=$(REACTOR)

show-instdir:
	@echo $(INSTDIR)/lib

PP=PYTHONPATH=$(PYTHONPATH)

.PHONY: make-version build
make-version:
	$(PYTHON) misc/make-version.py

build: make-version build-zfec build-Crypto build-foolscap build-simplejson
	$(PP) $(PYTHON) ./setup.py $(EXTRA_SETUP_ARGS) install --prefix="$(INSTDIR)" --install-lib="$(INSTDIR)/lib" --install-scripts="$(INSTDIR)/bin"

build-zfec:
	cd src/zfec &&  \
	$(PP) $(PYTHON) ./setup.py $(EXTRA_SETUP_ARGS) install --single-version-externally-managed --prefix="$(INSTDIR)" --record="$(INSTDIR)/zfec_install.log" --install-lib="$(INSTDIR)/lib" --install-scripts="$(INSTDIR)/bin"

build-foolscap:
	cd src/foolscap && \
	$(PP) $(PYTHON) ./setup.py $(EXTRA_SETUP_ARGS) install --prefix="$(INSTDIR)" --record="$(INSTDIR)/foolscap_install.log" --install-lib="$(INSTDIR)/lib" --install-scripts="$(INSTDIR)/bin"

build-simplejson:
	cd src/simplejson && \
	$(PP) $(PYTHON) ./setup.py $(EXTRA_SETUP_ARGS) install --prefix="$(INSTDIR)" --record="$(INSTDIR)/simplejson_install.log" --install-lib="$(INSTDIR)/lib" --install-scripts="$(INSTDIR)/bin"

build-Crypto:
	cd src/Crypto && \
	$(PP) $(PYTHON) ./setup.py $(EXTRA_SETUP_ARGS) install --prefix="$(INSTDIR)" --record="$(INSTDIR)/Crypto_install.log" --install-lib="$(INSTDIR)/lib" --install-scripts="$(INSTDIR)/bin"

clean-zfec:
	-cd src/zfec && \
	$(PP) $(PYTHON) ./setup.py clean --all

clean-foolscap:
	-cd src/foolscap && \
	$(PP) $(PYTHON) ./setup.py clean --all

clean-Crypto:
	cd src/Crypto && \
	$(PP) $(PYTHON) ./setup.py clean --all


# RUNNING
#
# these targets let you create a client node in the current directory and
# start/stop it.

.PHONY: create-client start-client stop-client run-client
.PHONY: create-introducer start-introducer stop-introducer

create-client: build
	$(PP) $(PYTHON) bin/allmydata-tahoe create-client -C CLIENTDIR
start-client: build
	$(PP) $(PYTHON) bin/allmydata-tahoe start -C CLIENTDIR
stop-client: build
	$(PP) $(PYTHON) bin/allmydata-tahoe stop -C CLIENTDIR

create-introducer: build
	$(PP) $(PYTHON) bin/allmydata-tahoe create-introducer -C INTRODUCERDIR
start-introducer: build
	$(PP) $(PYTHON) bin/allmydata-tahoe start -C INTRODUCERDIR
stop-introducer: build
	$(PP) $(PYTHON) bin/allmydata-tahoe stop -C INTRODUCERDIR



# TESTING

.PHONY: test-all test test-foolscap test-figleaf figleaf-output

# you can use 'make test TEST=allmydata.test.test_introducer' to run just a
# specific test. TEST=allmydata.test.test_client.Basic.test_permute works
# too.
TEST=allmydata zfec
REPORTER=

test-all: test-foolscap test

# use 'make test REPORTER=--reporter=bwverbose' from buildbot, to supress the
# ansi color sequences

test: build
	$(PP) $(TRIAL) $(REPORTER) $(TEST)

# foolscap tests need to be run in their own source dir, so that the paths to
# the .pyc files are correct (since some of the foolscap tests depend upon
# stack traces having actual source code in them, and they don't when the
# tests are run from the 'instdir' that the tahoe makefile uses).
test-foolscap:
	cd src/foolscap && PYTHONPATH=$(ORIGPYTHONPATH) $(TRIAL) $(REPORTER) foolscap

test-figleaf: build
	find $(INSTDIR) -name '*.pyc' |xargs rm
	rm -f .figleaf
	$(PP) $(TRIAL) --reporter=bwverbose-figleaf $(TEST)

figleaf-output:
	$(PP) $(PYTHON) misc/figleaf2html -d coverage-html -r $(INSTDIR)/lib -x misc/figleaf.excludes
	@echo "now point your browser at coverage-html/index.html"

# after doing test-figleaf and figleaf-output, point your browser at
# coverage-html/index.html

.PHONY: upload-figleaf .figleaf.el pyflakes count-lines check-memory clean

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
	$(PP) $(PYTHON) misc/figleaf2el.py .figleaf $(INSTDIR)/lib

pyflakes:
	$(PP) $(PYTHON) -OOu `which pyflakes` src/allmydata

count-lines:
	@echo -n "files: "
	@find src -name '*.py' |grep -v /build/ |wc --lines
	@echo -n "lines: "
	@cat `find src -name '*.py' |grep -v /build/` |wc --lines
	@echo -n "TODO: "
	@grep TODO `find src -name '*.py' |grep -v /build/` | wc --lines

check-memory: build
	$(PP) $(PYTHON) src/allmydata/test/check_memory.py

test-darcs-boringfile:
	$(MAKE)
	$(PYTHON) misc/test-darcs-boringfile.py

test-clean:
	find . |sort >allfiles.tmp.old
	$(MAKE)
	$(MAKE) clean
	find . |grep -v allfiles.tmp |sort >allfiles.tmp.new
	diff allfiles.tmp.old allfiles.tmp.new

clean: clean-zfec clean-Crypto clean-foolscap
	rm -rf build
	rm -f debian
	rm -rf instdir

# DEBIAN PACKAGING

VER=$(shell $(PYTHON) misc/get-version.py)
DEBCOMMENTS="'make deb' build"

show-version:
	@echo $(VER)

.PHONY: setup-dapper setup-sid setup-edgy setup-feisty setup-etch
.PHONY: deb-dapper deb-sid deb-edgy deb-feisty deb-etch
.PHONY: increment-deb-version
.PHONY: deb-dapper-head deb-sid-head deb-edgy-head deb-feisty-head
.PHONY: deb-etch-head

setup-dapper:
	rm -f debian
	ln -s misc/dapper/debian debian
	chmod a+x debian/rules

setup-sid:
	rm -f debian
	ln -s misc/sid/debian debian
	chmod a+x debian/rules

# edgy uses the feisty control files for now
setup-edgy:
	rm -f debian
	ln -s misc/feisty/debian debian
	chmod a+x debian/rules

setup-feisty:
	rm -f debian
	ln -s misc/feisty/debian debian
	chmod a+x debian/rules

# etch uses the fesity control files for now
setup-etch:
	rm -f debian
	ln -s misc/feisty/debian debian
	chmod a+x debian/rules


deb-dapper: setup-dapper
	fakeroot debian/rules binary && \
	make -C src/foolscap debian-dapper && \
	mv src/python-foolscap*.deb .. && \
	echo && \
	echo "The newly built .deb packages are in the parent directory from here."

deb-sid: setup-sid
	fakeroot debian/rules binary && \
	make -C src/foolscap debian-sid && \
	mv src/python-foolscap*.deb .. && \
	echo && \
	echo "The newly built .deb packages are in the parent directory from here."

deb-edgy: setup-edgy
	fakeroot debian/rules binary && \
	make -C src/foolscap debian-edgy && \
	mv src/python-foolscap*.deb .. && \
	echo && \
	echo "The newly built .deb packages are in the parent directory from here."

deb-feisty: setup-feisty
	fakeroot debian/rules binary && \
	make -C src/foolscap debian-feisty && \
	mv src/python-foolscap*.deb .. && \
	echo && \
	echo "The newly built .deb packages are in the parent directory from here."

deb-etch: setup-etch
	fakeroot debian/rules binary && \
	make -C src/foolscap debian-sid && \
	mv src/python-foolscap*.deb .. && \
	echo && \
	echo "The newly built .deb packages are in the parent directory from here."

increment-deb-version: make-version
	debchange --newversion $(VER) $(DEBCOMMENTS)
deb-dapper-head: setup-dapper increment-deb-version
	fakeroot debian/rules binary
deb-sid-head: setup-sid increment-deb-version
	fakeroot debian/rules binary
deb-edgy-head: setup-edgy increment-deb-version
	fakeroot debian/rules binary
deb-feisty-head: setup-feisty increment-deb-version
	fakeroot debian/rules binary
deb-etch-head: setup-etch increment-deb-version
	fakeroot debian/rules binary

