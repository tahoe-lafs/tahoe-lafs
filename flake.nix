{
  description = "Tahoe-LAFS, free and open decentralized data store";

  inputs = {
    # Two alternate nixpkgs pins.  Ideally these could be selected easily from
    # the command line but there seems to be no syntax/support for that.
    # However, these at least cause certain revisions to be pinned in our lock
    # file where you *can* dig them out - and the CI configuration does.
    "nixpkgs-22_11" = {
      url = github:NixOS/nixpkgs?ref=nixos-22.11;
    };
    "nixpkgs-unstable" = {
      url = github:NixOS/nixpkgs;
    };

    # Point the default nixpkgs at one of those.  This avoids having getting a
    # _third_ package set involved and gives a way to provide what should be a
    # working experience by default (that is, if nixpkgs doesn't get
    # overridden).
    nixpkgs.follows = "nixpkgs-22_11";

    # Also get flake-utils for simplified multi-system definitions.
    flake-utils = {
      url = github:numtide/flake-utils;
    };
  };

  outputs = { self, nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (system: let
      # First get the package set for this system architecture.
      pkgs = nixpkgs.legacyPackages.${system};

      # Here are the versions of Python that we will automatically expose apps
      # and packages for.  Note that they may not *all* work against a single
      # version of nixpkgs!  These are the names of Python packages available
      # from nixpkgs.  As a result CPython represented as "pythonXX" here (but
      # still identified as "cpython" in the outputs).
      pythonVersions = builtins.filter (name: pkgs ? ${name}) [
        "python38"
        "python39"
        "python310"
        "python311"
        "pypy38"
        "pypy39"
      ];

      # Retrieve the actual Python package for each configured version, and
      # configure each with our Python package overrides.
      pythons = builtins.map (pyVer: makePython pkgs.${pyVer}) pythonVersions;

      inherit (import ./nix/lib.nix)
        pythonVersion
        mergeSets
        makeApps
        makePython
        makePackage
        unitTestsApp
        integrationTestsApp
        tahoeApp;

      # python -> string
      #
      # Construct the Tahoe-LAFS package name for the given Python runtime.
      packageName = python: "${pythonVersion python}-tahoe-lafs";

      # python -> string
      #
      # Construct the unit test application name for the given Python runtime.
      unitTestName = python: "${pythonVersion python}-unit";

      # python -> string
      #
      # Construct the integration test application name for the given Python
      # runtime.
      integrationTestName = python: "${pythonVersion python}-integration";

    in {
      packages = (
        # Define a package for every configured version of Python.
        makeApps packageName (python: makePackage python.pkgs) pythons // {
          # And make the default package the same as the package for one of
          # the Python versions.
          default = self.packages.${system}."${packageName (builtins.head pythons)}";
        });
      apps = mergeSets
        [
          # Define apps for running the unit tests against each configured
          # version of Python.
          (makeApps unitTestName (unitTestsApp pkgs) pythons)

          # Define apps for running the integration tests against those Python
          # versions.
          (makeApps integrationTestName (integrationTestsApp pkgs) pythons)

          # Define apps for running Tahoe-LAFS itself against those Python
          # versions.  Apps can share the same names as packages.  It's a good
          # name and we're in a different namespace.
          (makeApps packageName tahoeApp pythons)

          # Make a default app that also runs one of the Tahoe-LAFS apps.
          { default = self.apps.${system}.${packageName (builtins.head pythons)}; }
        ];
    });
}
