# Python 3 porting targets
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


# Top-level, phony targets

.PHONY: default
default:
	@echo "no default target"

.PHONY: test-py3-all-before
## Log the output of running all tests under Python 3 before changes
test-py3-all-before: ../../.tox/make-test-py3-all-old.log
.PHONY: test-py3-all-diff
## Compare the output of running all tests under Python 3 after changes
test-py3-all-diff: ../../.tox/make-test-py3-all.diff


# Real targets

# Gauge the impact of changes on Python 3 compatibility
# Compare the output from running all tests under Python 3 before and after changes.
# Before changes:
# `$ rm -f .tox/make-test-py3-all-*.log && make .tox/make-test-py3-all-old.log`
# After changes:
# `$ make .tox/make-test-py3-all.diff`
$(foreach side,old new,../../.tox/make-test-py3-all-$(side).log):
	cd "../../"
	tox --develop --notest -e py36-coverage
	(make VIRTUAL_ENV=./.tox/py36-coverage TEST_SUITE=allmydata \
		test-venv-coverage || true) | \
		sed -E 's/\([0-9]+\.[0-9]{3} secs\)/(#.### secs)/' | \
		tee "./misc/python3/$(@)"
../../.tox/make-test-py3-all.diff: ../../.tox/make-test-py3-all-new.log
	(diff -u "$(<:%-new.log=%-old.log)" "$(<)" || true) | tee "$(@)"

# Locate modules that are candidates for naively converting `unicode` -> `str`.
# List all Python source files that reference `unicode` but don't reference `str`
../../.tox/py3-unicode-no-str.ls:
	cd "../../"
	find src -type f -iname '*.py' -exec grep -l -E '\Wunicode\W' '{}' ';' | \
		xargs grep -L '\Wstr\W' | xargs ls -ld | tee "./misc/python3/$(@)"
