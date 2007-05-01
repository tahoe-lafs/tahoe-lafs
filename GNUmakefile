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

.PHONY: build
build: build-zfec build-Crypto build-foolscap
	$(PP) $(PYTHON) ./setup.py $(EXTRA_SETUP_ARGS) install --prefix="." --root="$(INSTDIR)" --install-lib="lib" --install-scripts="bin"

build-zfec:
	cd src/zfec &&  \
	$(PP) $(PYTHON) ./setup.py $(EXTRA_SETUP_ARGS) install --prefix="." --root="$(INSTDIR)" --install-lib="lib" --install-scripts="bin"

build-foolscap:
	cd src/foolscap && \
	$(PP) $(PYTHON) ./setup.py $(EXTRA_SETUP_ARGS) install --prefix="." --root="$(INSTDIR)" --install-lib="lib" --install-scripts="bin"

build-Crypto:
	cd src/Crypto && \
	$(PP) $(PYTHON) ./setup.py $(EXTRA_SETUP_ARGS) install --prefix="." --root="$(INSTDIR)" --install-lib="lib" --install-scripts="bin"

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

.PHONY: test

ifeq ($(TEST),)
TEST=allmydata zfec
endif
REPORTER=

# use 'make test REPORTER=--reporter=bwverbose' from buildbot, to supress the
# ansi color sequences
test: build
	$(PP) $(TRIAL) $(REPORTER) $(TEST) ;
	cd src/foolscap && $(PP) $(TRIAL) $(REPORTER) foolscap

test-figleaf: build
	rm -f .figleaf
	$(PP) $(TRIAL) --reporter=bwverbose-figleaf $(TEST)

figleaf-output:
	$(PP) $(PYTHON) misc/figleaf2html -d coverage-html -r $(INSTDIR)/lib -x misc/figleaf.excludes
	@echo "now point your browser at coverage-html/index.html"
# after doing test-figleaf and figleaf-output, point your browser at
# coverage-html/index.html

# this command is meant to be run with an
ifdef UPLOAD_TARGET
upload-figleaf:
	rsync -a coverage-html/ $(UPLOAD_TARGET)
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

check-memory:
	$(PP) $(PYTHON) src/allmydata/test/check_memory.py

clean: clean-zfec clean-Crypto clean-foolscap
	rm -rf build
	rm -f debian
	rm -rf instdir

create_dirs:
	mkdir -p introducer_and_vdrive-basedir
	mkdir -p client-basedir
	mkdir -p client-basedir2
	mkdir -p client-basedir/storage
	mkdir -p client-basedir2/storage

DEBVER=`head -1 debian/changelog | sed -e 's/^[^(]*(\([^)]*\)).*$$/\1/' | sed -e 's/^\([0-9]\+\.[0-9]\+\.[0-9]\+\).*$$/\1/'`
DEBSTRING=$(DEBVER)-T`date +%s`
DEBCOMMENTS="'make deb' build"

show:
	echo $(DEBVER)
	echo $(DEBSTRING)

.PHONY: setup-dapper setup-sid setup-edgy setup-feisty
.PHONY: deb-dapper deb-sid deb-edgy deb-feisty
.PHONY: increment-deb-version
.PHONY: deb-dapper-head deb-sid-head deb-edgy-head deb-feisty-head

setup-dapper:
	rm -f debian
	ln -s dapper/debian debian
	chmod a+x debian/rules

setup-sid:
	rm -f debian
	ln -s sid/debian debian
	chmod a+x debian/rules

# edgy uses the feisty control files for now
setup-edgy:
	rm -f debian
	ln -s feisty/debian debian
	chmod a+x debian/rules

setup-feisty:
	rm -f debian
	ln -s feisty/debian debian
	chmod a+x debian/rules


deb-dapper: setup-dapper
	fakeroot debian/rules binary
deb-sid: setup-sid
	fakeroot debian/rules binary
deb-edgy: setup-edgy
	fakeroot debian/rules binary
deb-feisty: setup-feisty
	fakeroot debian/rules binary

increment-deb-version:
	debchange --newversion $(DEBSTRING) $(DEBCOMMENTS)
deb-dapper-head: setup-dapper increment-deb-version
	fakeroot debian/rules binary
deb-sid-head: setup-sid increment-deb-version
	fakeroot debian/rules binary
deb-edgy-head: setup-edgy increment-deb-version
	fakeroot debian/rules binary
deb-feisty-head: setup-feisty increment-deb-version
	fakeroot debian/rules binary

