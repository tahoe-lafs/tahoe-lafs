from distutils.core import setup
import py2exe

import glob

setup_args = {
    'name': 'Tahoe',
    'description': 'Allmydata Tahoe distributated storage',
    'author': 'Allmydata, Inc.',
    'windows': [
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
        ('web', glob.glob('../src/allmydata/web/*')),
    ],
    'zipfile' : 'library.zip',
    'options': {
        "py2exe": {
            "excludes": [
                "pkg_resources",
            ],
            "includes": [
            ],
            "packages": [
                "encodings",
                "_xmlplus",
            ],
            #"optimize" : 2,
        },
    },
}

if __name__ == '__main__':
    setup(**setup_args)


_junk = py2exe # appease pyflakes
del _junk
