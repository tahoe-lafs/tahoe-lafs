import os

import setuptools

from darcsver import darcsvermodule

from distutils.errors import DistutilsSetupError

def validate_string_or_iter_of_strings(dist, attr, value):
    # value is required to be a string or else a list of strings
    if isinstance(value, basestring):
        return
    try:
        for thing in value:
            if not isinstance(thing, basestring):
                raise DistutilsSetupError("%r is required to be a string or an iterable of strings (got %r)" % (attr, value))
    except TypeError:
        raise DistutilsSetupError("%r is required to be a string or an iterable of strings (got %r)" % (attr, value))

def validate_versionfiles(dist, attr, value):
    return validate_string_or_iter_of_strings(dist, attr, value)

def validate_versionbodies(dist, attr, value):
    return validate_string_or_iter_of_strings(dist, attr, value)

PYTHON_VERSION_BODY='''
# This is the version of this tree, as created by %(versiontool)s from the darcs patch
# information: the main version number is taken from the most recent release
# tag. If some patches have been added since the last release, this will have a
# -NN "build number" suffix, or else a -rNN "revision number" suffix. Please see
# pyutil.version_class for a description of what the different fields mean.

__pkgname__ = "%(pkgname)s"
verstr = "%(pkgversion)s"
try:
    from pyutil.version_class import Version as pyutil_Version
    __version__ = pyutil_Version(verstr)
except (ImportError, ValueError):
    # Maybe there is no pyutil installed, or this may be an older version of
    # pyutil.version_class which does not support SVN-alike revision numbers.
    from distutils.version import LooseVersion as distutils_Version
    __version__ = distutils_Version(verstr)
'''

class DarcsVer(setuptools.Command):
    description = "generate a version number from darcs history"
    user_options = [
        ('project-name', None, "name of the project as it appears in the project's release tags (default's the to the distribution name)"),
        ('version-file', None, "path to file into which the version number should be written (defaults to the package directory's _version.py)"),
        ('count-all-patches', None, "If true, count the total number of patches in all history.  If false, count the total number of patches since the most recent release tag."),
        ('abort-if-snapshot', None, "If true, the if the current version is a snapshot (not a release tag), then immediately exit the process with exit code 0."),
        ]

    def initialize_options(self):
        self.project_name = None
        self.version_file = None
        self.count_all_patches = None
        self.abort_if_snapshot = None

    def finalize_options(self):
        if self.project_name is None:
            self.project_name = self.distribution.get_name()

        # If the user passed --version-file on the cmdline, override
        # the setup.py's versionfiles argument.
        if self.version_file is not None:
            self.distribution.versionfiles = [self.version_file]

        if self.abort_if_snapshot is None:
            self.abort_if_snapshot=False

    def run(self):
        if self.distribution.versionfiles is None:
            toppackage = ''
            # If there is a package with the same name as the project name and
            # there is a directory by that name then use that.
            packagedir = None
            if self.distribution.packages and self.project_name in self.distribution.packages:
                toppackage = self.project_name
                srcdir = ''
                if self.distribution.package_dir:
                    srcdir = self.distribution.package_dir.get(toppackage)
                    if not srcdir is None:
                        srcdir = self.distribution.package_dir.get('', '')
                packagedir = os.path.join(srcdir, toppackage)

            if packagedir is None or not os.path.isdir(packagedir):
                # Else, if there is a singly-rooted tree of packages, use the
                # root of that.
                if self.distribution.packages:
                    for package in self.distribution.packages:
                        if not toppackage:
                            toppackage = package
                        else:
                            if toppackage.startswith(package+"."):
                                toppackage = package
                            else:
                                if not package.startswith(toppackage+"."):
                                    # Not singly-rooted
                                    toppackage = ''
                                    break

                srcdir = ''
                if self.distribution.package_dir:
                    srcdir = self.distribution.package_dir.get(toppackage)
                    if srcdir is None:
                        srcdir = self.distribution.package_dir.get('', '')
                packagedir = os.path.join(srcdir, toppackage)

            self.distribution.versionfiles = [os.path.join(packagedir, '_version.py')]

        if self.distribution.versionbodies is None:
            self.distribution.versionbodies = [PYTHON_VERSION_BODY]

        (rc, verstr) = darcsvermodule.update(self.project_name, self.distribution.versionfiles, self.count_all_patches, abort_if_snapshot=self.abort_if_snapshot, EXE_NAME="setup.py darcsver", version_body=self.distribution.versionbodies)
        self.distribution.metadata.version = verstr
