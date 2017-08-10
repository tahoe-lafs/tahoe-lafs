# -*- mode: python -*-

from __future__ import print_function

from distutils.sysconfig import get_python_lib
import hashlib
import os
import platform
import shutil
import struct
import sys


if not hasattr(sys, 'real_prefix'):
    sys.exit("Please run inside a virtualenv with Tahoe-LAFS installed.")


# Ugly hack to disable the setuptools requirement asserted in '_auto_deps.py'.
# Without patching out this requirement, frozen binaries will fail at runtime.
autodeps_path = os.path.join(get_python_lib(), 'allmydata', '_auto_deps.py')
print("Patching '{}' to remove setuptools check...".format(autodeps_path))
autodeps_path_backup = autodeps_path + '.backup'
shutil.copy2(autodeps_path, autodeps_path_backup)
with open(autodeps_path_backup) as src, open(autodeps_path, 'w+') as dest:
    dest.write(src.read().replace('"setuptools >=', '#"setuptools >='))
print("Done!")


options = [('u', None, 'OPTION')]  # Unbuffered stdio

added_files = [
    ('COPYING.*', '.'),
    ('CREDITS', '.'),
    ('relnotes.txt', '.'),
    ('src/allmydata/web/*.xhtml', 'allmydata/web'),
    ('src/allmydata/web/static/*', 'allmydata/web/static'),
    ('src/allmydata/web/static/css/*', 'allmydata/web/static/css'),
    ('src/allmydata/web/static/img/*.png', 'allmydata/web/static/img')]

a = Analysis(
    ['static/tahoe.py'],
    pathex=[],
    binaries=None,
    datas=added_files,
    hiddenimports=['characteristic', 'cffi'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    options,
    exclude_binaries=True,
    name='tahoe',
    debug=False,
    strip=False,
    upx=False,
    console=True)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='Tahoe-LAFS')


# Revert the '_auto_deps.py' patch above
shutil.move(autodeps_path_backup, autodeps_path)


print("Creating archive...")
platform_tag = platform.system().replace('Darwin', 'MacOS')
bitness_tag = str(struct.calcsize('P') * 8) + 'bit'
archive_name = 'Tahoe-LAFS-{}-{}'.format(platform_tag, bitness_tag)
if sys.platform == 'win32':
    archive_format = 'zip'
    archive_suffix = '.zip'
else:
    archive_format = 'gztar'
    archive_suffix = '.tar.gz'
base_name = os.path.join('dist', archive_name)
shutil.make_archive(base_name, archive_format, 'dist', 'Tahoe-LAFS')

print("Hashing (SHA256)...")
archive_path = base_name + archive_suffix
hasher = hashlib.sha256()
with open(archive_path, 'rb') as f:
    for block in iter(lambda: f.read(4096), b''):
        hasher.update(block)
print("{}  {}".format(hasher.hexdigest(), archive_path))
