
# we get $(PYTHON) from our parent, do 'make windows-exe PYTHON=foo' to
# control it, since 'PYTHON=foo make windows-exe' doesn't seem to override
# the default.

# We also get $(PYTHONPATH) from our parent, which is critical for py2exe to
# find the tahoe code. Invoking this Makefile directly won't work.

INNOSETUP := $(shell cygpath -au "$(PROGRAMFILES)/Inno Setup 5/Compil32.exe")

.PHONY: windows-exe windows-installer windows-installer-upload

windows-exe.stamp:
	$(PYTHON) setup.py py2exe
	touch windows-exe.stamp
windows-exe: windows-exe.stamp

windows-installer: windows-exe.stamp
	$(PYTHON) ../misc/sub-ver.py installer.tmpl >installer.iss
	"$(INNOSETUP)" /cc installer.iss

windows-installer-upload:
	chmod -R o+rx dist/installer
	rsync -av -e /usr/bin/ssh dist/installer/ amduser@dev:/home/amduser/public_html/dist/tahoe/windows/

