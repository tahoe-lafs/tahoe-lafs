
# we get $(PYTHON) from our parent, do 'make windows-exe PYTHON=foo' to
# control it, since 'PYTHON=foo make windows-exe' doesn't seem to trigger the
# export.

INNOSETUP := $(shell cygpath -au "$(PROGRAMFILES)/Inno Setup 5/Compil32.exe")

windows-exe:
	$(PYTHON) setup.py py2exe

windows-installer: windows-exe
	$(PYTHON) ../sub-ver.py installer.tmpl >installer.iss
	$(INNOSETUP) /cc installer.iss

windows-installer-upload:
	chmod -R o+rx dist/installer
	rsync -av -e /usr/bin/ssh dist/installer/ amduser@dev:/home/amduser/public_html/dist/tahoe/windows/

