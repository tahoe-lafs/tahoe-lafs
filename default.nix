let
  # sources.nix contains information about which versions of some of our
  # dependencies we should use.  since we use it to pin nixpkgs and the PyPI
  # package database, roughly all the rest of our dependencies are *also*
  # pinned - indirectly.
  #
  # sources.nix is managed using a tool called `niv`.  as an example, to
  # update to the most recent version of nixpkgs from the 21.11 maintenance
  # release, in the top-level tahoe-lafs checkout directory you run:
  #
  #   niv update nixpkgs-21.11
  #
  # or, to update the PyPI package database -- which is necessary to make any
  # newly released packages visible -- you likewise run:
  #
  #   niv update pypi-deps-db
  #
  # niv also supports chosing a specific revision, following a different
  # branch, etc.  find complete documentation for the tool at
  # https://github.com/nmattia/niv
  sources = import nix/sources.nix;
in
{
  pkgsVersion ? "nixpkgs-21.11" # a string which chooses a nixpkgs from the
                                # niv-managed sources data

, pkgs ? import sources.${pkgsVersion} { } # nixpkgs itself

, pypiData ? sources.pypi-deps-db # the pypi package database snapshot to use
                                  # for dependency resolution

, pythonVersion ? "python37" # a string choosing the python derivation from
                             # nixpkgs to target

, extras ? [ "tor" "i2p" ] # a list of strings identifying tahoe-lafs extras,
                           # the dependencies of which the resulting package
                           # will also depend on.  Include all of the runtime
                           # extras by default because the incremental cost of
                           # including them is a lot smaller than the cost of
                           # re-building the whole thing to add them.

, mach-nix ? import sources.mach-nix { # the mach-nix package to use to build
                                       # the tahoe-lafs package
    inherit pkgs pypiData;
    python = pythonVersion;
}
}:
# The project name, version, and most other metadata are automatically
# extracted from the source.  Some requirements are not properly extracted
# and those cases are handled below.  The version can only be extracted if
# `setup.py update_version` has been run (this is not at all ideal but it
# seems difficult to fix) - so for now just be sure to run that first.
mach-nix.buildPythonPackage rec {
  # Define the location of the Tahoe-LAFS source to be packaged.  Clean up all
  # as many of the non-source files (eg the `.git` directory, `~` backup
  # files, nix's own `result` symlink, etc) as possible to avoid needing to
  # re-build when files that make no difference to the package have changed.
  src = pkgs.lib.cleanSource ./.;

  # Select whichever package extras were requested.
  inherit extras;

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

    # mach-nix does not yet parse environment markers (e.g. "python > '3.0'")
    # correctly. It misses all of our requirements which have an environment marker.
    # Duplicate them here.
    foolscap
    eliot
    pyrsistent
    collections-extended
  '';

  # Specify where mach-nix should find packages for our Python dependencies.
  # There are some reasonable defaults so we only need to specify certain
  # packages where the default configuration runs into some issue.
  providers = {
  };

  # Define certain overrides to the way Python dependencies are built.
  _ = {
    # Remove a click-default-group patch for a test suite problem which no
    # longer applies because the project apparently no longer has a test suite
    # in its source distribution.
    click-default-group.patches = [];
  };

  passthru.meta.mach-nix = {
    inherit providers _;
  };
}
