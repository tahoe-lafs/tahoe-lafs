import os

import setuptools

from darcsver import darcsvermodule

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

        if self.version_file is None:
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

            self.version_file = os.path.join(packagedir, '_version.py')

        if self.abort_if_snapshot is None:
            self.abort_if_snapshot=False

    def run(self):
        (rc, verstr) = darcsvermodule.update(self.project_name, self.version_file, self.count_all_patches, abort_if_snapshot=self.abort_if_snapshot, EXE_NAME="setup.py darcsver")
        self.distribution.metadata.version = verstr
