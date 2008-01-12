
INNOSETUP := $(shell cygpath -u "$(PROGRAMFILES)/Inno Setup 5/Compile32.exe")

# note that this requires the python path to be set appropriately, and hence this
# should be invoked by calling the windows-exe taget in the top level makefile

windows-exe:
	$(PYTHON) setup.py py2exe

installer:
	$(INNOSETUP)
	#$(INNOSETUP) /cc installer.iss
