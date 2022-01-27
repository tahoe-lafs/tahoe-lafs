let
  sources = import nix/sources.nix;
in
{ pkgsVersion ? "nixpkgs-21.11"
, pkgs ? import sources.${pkgsVersion} { }
, pypiData ? sources.pypi-deps-db
, pythonVersion ? "python37"
, mach-nix ? import sources.mach-nix {
    inherit pkgs pypiData;
    python = pythonVersion;
  }
}:
# The project name, version, and most other metadata are automatically
# extracted from the source.  Some requirements are not properly extracted
# and those cases are handled below.  The version can only be extracted if
# `setup.py update_version` has been run (this is not at all ideal but it
# seems difficult to fix) - so for now just be sure to run that first.
mach-nix.buildPythonPackage {
  # Define the location of the Tahoe-LAFS source to be packaged.  Clean up all
  # as many of the non-source files (eg the `.git` directory, `~` backup
  # files, nix's own `result` symlink, etc) as possible to avoid needing to
  # re-build when files that make no difference to the package have changed.
  src = pkgs.lib.cleanSource ./.;

  # Define some extra requirements that mach-nix does not automatically detect
  # from inspection of the source.  We typically don't need to put version
  # constraints on any of these requirements.  The pypi-deps-db we're
  # operating with makes dependency resolution deterministic so as long as it
  # works once it will always work.  It could be that in the future we update
  # pypi-deps-db and an incompatibility arises - in which case it would make
  # sense to apply some version constraints here.
  requirementsExtra = ''
    # mach-nix does not yet support pyproject.toml which means it misses any
    # build-time requirements of our dependencies which are declared in such a
    # file.  Tell it about them here.
    setuptools_rust

    # mach-nix does not yet parse environment markers correctly.  It misses
    # all of our requirements which have an environment marker.  Duplicate them
    # here.
    foolscap
    eliot
    pyrsistent
  '';

  providers = {
    # Through zfec 1.5.5 the wheel has an incorrect runtime dependency
    # declared on argparse, not available for recent versions of Python 3.
    # Force mach-nix to use the sdist instead.  This allows us to apply a
    # patch that removes the offending declaration.
    zfec = "sdist";
  };

  # Define certain overrides to the way Python dependencies are built.
  _ = {
    # Apply the argparse declaration fix to zfec sdist.
    zfec.patches = with pkgs; [
      (fetchpatch {
        name = "fix-argparse.patch";
        url = "https://github.com/tahoe-lafs/zfec/commit/c3e736a72cccf44b8e1fb7d6c276400204c6bc1e.patch";
        sha256 = "1md9i2fx1ya7mgcj9j01z58hs3q9pj4ch5is5b5kq4v86cf6x33x";
      })
    ];
  };

}
