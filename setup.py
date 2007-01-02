#! /usr/bin/python

#from setuptools import setup, find_packages
from distutils.core import setup
from distutils.core import Extension
from distutils.command.build_ext import build_ext
import os, sys


# we build and install a copy of pycrypto as allmydata.Crypto, because we've
# made some improvements that have not yet made it upstream (specifically a
# much faster version of CTR mode). To accomplish this, we must include a
# couple of pieces from the pycrypto setup.py file here.

if sys.platform == 'win32':
    HTONS_LIBS = ['ws2_32']
    plat_ext = [
                Extension("Crypto.Util.winrandom",
                          libraries = HTONS_LIBS + ['advapi32'],
                          include_dirs=['src/Crypto/src/'],
                          extra_compile_args=['-O0 -g',],
                          extra_link_args=['-g',],
                          undef_macros=['NDEBUG',],
                          sources=["src/Crypto/src/winrand.c"],
                          **debug_build_kw)
               ]
else:
    HTONS_LIBS = []
    plat_ext = []

# Functions for finding libraries and files, copied from Python's setup.py.

def find_file(filename, std_dirs, paths):
    """Searches for the directory where a given file is located,
    and returns a possibly-empty list of additional directories, or None
    if the file couldn't be found at all.

    'filename' is the name of a file, such as readline.h or libcrypto.a.
    'std_dirs' is the list of standard system directories; if the
        file is found in one of them, no additional directives are needed.
    'paths' is a list of additional locations to check; if the file is
        found in one of them, the resulting list will contain the directory.
    """

    # Check the standard locations
    for dir in std_dirs:
        f = os.path.join(dir, filename)
        if os.path.exists(f): return []

    # Check the additional directories
    for dir in paths:
        f = os.path.join(dir, filename)
        if os.path.exists(f):
            return [dir]

    # Not found anywhere
    return None

def find_library_file(compiler, libname, std_dirs, paths):
    filename = compiler.library_filename(libname, lib_type='shared')
    result = find_file(filename, std_dirs, paths)
    if result is not None: return result

    filename = compiler.library_filename(libname, lib_type='static')
    result = find_file(filename, std_dirs, paths)
    return result


def cc_remove_option (compiler, option):
    """
    Remove option from Unix-style compiler.
    """
    for optlist in (compiler.compiler, compiler.compiler_so):
        if option in optlist:
            optlist.remove(option)


class PCTBuildExt (build_ext):
    def build_extensions(self):
        debug_build_kw = {}
        self.extensions += [
            # Hash functions
            Extension("allmydata.Crypto.Hash.MD4",
                      include_dirs=['src/Crypto/src/'],
                      sources=["src/Crypto/src/MD4.c"],
                      **debug_build_kw),
            Extension("allmydata.Crypto.Hash.SHA256",
                      include_dirs=['src/Crypto/src/'],
                      sources=["src/Crypto/src/SHA256.c"],
                      **debug_build_kw),

            # Block encryption algorithms
            Extension("allmydata.Crypto.Cipher.AES",
                      include_dirs=['src/Crypto/src/'],
                      sources=["src/Crypto/src/AES.c"],
                      **debug_build_kw),
            Extension("allmydata.Crypto.Cipher.ARC2",
                      include_dirs=['src/Crypto/src/'],
                      sources=["src/Crypto/src/ARC2.c"],
                      **debug_build_kw),
            Extension("allmydata.Crypto.Cipher.Blowfish",
                      include_dirs=['src/Crypto/src/'],
                      sources=["src/Crypto/src/Blowfish.c"],
                      **debug_build_kw),
            Extension("allmydata.Crypto.Cipher.CAST",
                      include_dirs=['src/Crypto/src/'],
                      sources=["src/Crypto/src/CAST.c"],
                      **debug_build_kw),
            Extension("allmydata.Crypto.Cipher.DES",
                      include_dirs=['src/Crypto/src/'],
                      sources=["src/Crypto/src/DES.c"],
                      **debug_build_kw),
            Extension("allmydata.Crypto.Cipher.DES3",
                      include_dirs=['src/Crypto/src/'],
                      sources=["src/Crypto/src/DES3.c"],
                      **debug_build_kw),

            # Stream ciphers
            Extension("allmydata.Crypto.Cipher.ARC4",
                      include_dirs=['src/Crypto/src/'],
                      sources=["src/Crypto/src/ARC4.c"],
                      **debug_build_kw),
            Extension("allmydata.Crypto.Cipher.XOR",
                      include_dirs=['src/Crypto/src/'],
                      sources=["src/Crypto/src/XOR.c"],
                      **debug_build_kw),
            ]

        # Detect which modules should be compiled
        self.detect_modules()
        if self.compiler.compiler_type == 'unix':
            if os.uname()[4] == 'm68k':
                # work around ICE on m68k machines in gcc 4.0.1
                cc_remove_option(self.compiler, "-O3")
        build_ext.build_extensions(self)

    def detect_modules (self):
        lib_dirs = self.compiler.library_dirs + ['/lib', '/usr/lib']
        inc_dirs = self.compiler.include_dirs + ['/usr/include']
        exts = []
        if (self.compiler.find_library_file(lib_dirs, 'gmp')):
            exts.append(Extension("allmydata.Crypto.PublicKey._fastmath",
                                  include_dirs=['src/Crypto/src/'],
                                  libraries=['gmp'],
                                  sources=["src/Crypto/src/_fastmath.c"]))
        self.extensions += exts


# these are the setup() args for the original pycrypto module. We only use a
# subset of them.
"""
kw = {'name':"pycrypto",
      'version':"2.0.1",
      'description':"Cryptographic modules for Python.",
      'author':"A.M. Kuchling",
      'author_email':"amk@amk.ca",
      'url':"http://www.amk.ca/python/code/crypto",

      'cmdclass' : {'build_ext':PCTBuildExt},
      'packages' : ["Crypto", "Crypto.Hash", "Crypto.Cipher", "Crypto.Util",
                  "Crypto.Protocol", "Crypto.PublicKey"],
      'package_dir' : { "Crypto":"." },
      # One module is defined here, because build_ext won't be
      # called unless there's at least one extension module defined.
      'ext_modules':[Extension("Crypto.Hash.MD2",
                             include_dirs=['src/'],
                             sources=["src/MD2.c"],
                               **debug_build_kw)],
      'classifiers': [
          'Development Status :: 4 - Beta',
          'License :: Public Domain',
          'Intended Audience :: Developers',
          'Operating System :: Unix',
          'Operating System :: Microsoft :: Windows',
          'Operating System :: MacOS :: MacOS X',
          'Topic :: Security :: Cryptography',
          ]
     }
"""

# this is our actual setup() call
setup(
    name="AllMyData",
    version="0.0.1",
    #packages=find_packages('.'),
    packages=["allmydata", "allmydata.test", "allmydata.util",
              "allmydata.scripts",
              "allmydata.Crypto", "allmydata.Crypto.Hash",
              "allmydata.Crypto.Cipher", "allmydata.Crypto.Util",
              "allmydata.Crypto.Protocol", "allmydata.Crypto.PublicKey",
              "allmydata.py_ecc",
              ],
    package_dir={ "allmydata": "src/allmydata",
                  "allmydata.Crypto": "src/Crypto",
                  "allmydata.py_ecc": "src/py_ecc",
                  },
    scripts = ["bin/allmydata"],
    package_data={ 'allmydata': ['web/*.xhtml'] },

    cmdclass= {'build_ext': PCTBuildExt},
      # One module is defined here, because build_ext won't be
      # called unless there's at least one extension module defined.
    ext_modules=[Extension("allmydata.Crypto.Hash.MD2",
                           include_dirs=['src/Crypto/src/'],
                           sources=["src/Crypto/src/MD2.c"])],

    description="AllMyData (tahoe2)",
    )

