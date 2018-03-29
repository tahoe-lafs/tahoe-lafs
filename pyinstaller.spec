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


options = [('u', None, 'OPTION')]  # Unbuffered stdio

added_files = [
    ('COPYING.*', '.'),
    ('CREDITS', '.'),
    ('relnotes.txt', '.'),
    ('src/allmydata/web/*.xhtml', 'allmydata/web'),
    ('src/allmydata/web/static/*', 'allmydata/web/static'),
    ('src/allmydata/web/static/css/*', 'allmydata/web/static/css'),
    ('src/allmydata/web/static/img/*.png', 'allmydata/web/static/img')]

hidden_imports = [
    'allmydata.client',
    'allmydata.introducer',
    'allmydata.stats',
    'cffi',
    'characteristic',
    'Crypto',
    'packaging.specifiers',
    'six.moves.html_parser',
    'yaml',
    'zfec'
]

a = Analysis(
    ['static/tahoe.py'],
    pathex=[],
    binaries=None,
    datas=added_files,
    hiddenimports=hidden_imports,
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
