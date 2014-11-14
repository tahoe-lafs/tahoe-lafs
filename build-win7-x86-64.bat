REM directory to store the build dependencies
mkdir dependencies

REM install VC Studio Express 2008
REM http://go.microsoft.com/?linkid=7729279

REM Install Windows SDK
REM http://www.microsoft.com/en-us/download/confirmation.aspx?id=3138

REM silently installing VC Studio 2008:
REM http://blogs.msdn.com/b/astebner/archive/2007/09/12/4887301.aspx
REM http://blogs.msdn.com/b/astebner/archive/2008/03/24/8334544.aspx

REM Download and install Python (2.7.8) x86-64 MSI.
set PATH=%PATH%;c:\Program files (x86)\GnuWin32\bin
wget https://www.python.org/ftp/python/2.7.8/python-2.7.8.msi -O dependencies/python-2.7.8.msi
 
start /wait dependencies/python-2.7.8.msi /passive
 
REM Download and install PyOpenSSL
wget https://pypi.python.org/packages/2.7/p/pyOpenSSL/pyOpenSSL-0.13.1.win-amd64-py2.7.exe#md5=223cc4ab7439818ccaf1bf7f51736dc8 -O dependencies/pyOpenSSL-0.13.1.win-amd64-py2.7.exe

start /wait dependencies/pyOpenSSL-0.13.1.win-amd64-py2.7.exe

REM open visual studio 2008 cmd prompt as Administrator (right click)
REM  Run the "Windows SDK Configuration Tool", select v7.0 and click "Make Current".

call "C:\Program Files (x86)\Microsoft Visual Studio 9.0\VC\bin\vcvars64.bat"

set MSSdk=1
set DISTUTILS_USE_SDK=1

regedit /s x64\VC_OBJECTS_PLATFORM_INFO.reg
regedit /s x64\600dd186-2429-11d7-8bf6-00b0d03daa06.reg
regedit /s x64\600dd187-2429-11d7-8bf6-00b0d03daa06.reg
regedit /s x64\600dd188-2429-11d7-8bf6-00b0d03daa06.reg
regedit /s x64\600dd189-2429-11d7-8bf6-00b0d03daa06.reg
regedit /s x64\656d875f-2429-11d7-8bf6-00b0d03daa06.reg
regedit /s x64\656d8760-2429-11d7-8bf6-00b0d03daa06.reg
regedit /s x64\656d8763-2429-11d7-8bf6-00b0d03daa06.reg
regedit /s x64\656d8766-2429-11d7-8bf6-00b0d03daa06.reg
copy "C:\Program Files (x86)\Microsoft Visual Studio 9.0\VC\vcpackages\AMD64.VCPlatform.config" "C:\Program Files (x86)\Microsoft Visual Studio 9.0\VC\vcpackages\AMD64.VCPlatform.Express.config"
copy "C:\Program Files (x86)\Microsoft Visual Studio 9.0\VC\vcpackages\Itanium.VCPlatform.config" "C:\Program Files (x86)\Microsoft Visual Studio 9.0\VC\vcpackages\Itanium.VCPlatform.Express.config"

REM Install OpenSSL

set PATH=%PATH%;c:\OpenSSL-win64\bin

REM build tahoe-lafs
c:\python27\python.exe setup.py build
