from distutils.core import setup
import py2exe

import glob

lnf_manifest = """
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1"
manifestVersion="1.0">
<assemblyIdentity
    version="0.64.1.0"
    processorArchitecture="x86"
    name="Controls"
    type="win32"
/>
<description>%s</description>
<dependency>
    <dependentAssembly>
        <assemblyIdentity
            type="win32"
            name="Microsoft.Windows.Common-Controls"
            version="6.0.0.0"
            processorArchitecture="X86"
            publicKeyToken="6595b64144ccf1df"
            language="*"
        />
    </dependentAssembly>
</dependency>
</assembly>
"""

packages = ['encodings']

try:
    import _xmlplus
except ImportError:
    pass
else:
    packages.append('_xmlplus')

setup_args = {
    'name': 'Tahoe',
    'description': 'Allmydata Tahoe distributated storage',
    'author': 'Allmydata, Inc.',
    'windows': [
        {
            'script': 'confwiz.py',
            'icon_resources': [(1, 'amdicon.ico')],
            'other_resources': [(24,1,lnf_manifest%'Allmydata Tahoe Config Wizard')],
        },
    ],
    'console': [
        'tahoe.py',
    ],
    'service': [
        'tahoesvc',
    ],
    'data_files': [
        ('.', [
        ],),
        ('pkg_resources/allmydata/web', glob.glob('../src/allmydata/web/*')),
        ('winfuse', glob.glob('./winfuse/*')),
    ],
    'zipfile' : 'library.zip',
    'options': {
        "py2exe": {
            "excludes": [
                "pkg_resources",
            ],
            "includes": [
            ],
            "packages": packages,
            #"optimize" : 2,
        },
    },
}

if __name__ == '__main__':
    setup(**setup_args)


_junk = py2exe # appease pyflakes
del _junk
