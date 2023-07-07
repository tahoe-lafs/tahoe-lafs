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
    "nixpkgs-23_05" = {
      url = github:NixOS/nixpkgs?ref=release-23.05;
    };
    "nixpkgs-unstable" = {
      url = github:NixOS/nixpkgs?ref=pull/238965/head;
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
    {
      # Expose an overlay which adds our version of Tahoe-LAFS to the Python
      # package sets we specify, as well as all of the correct versions of its
      # dependencies.
      #
      # We will also use this to define some other outputs since it gives us
      # the most succinct way to get a working Tahoe-LAFS package.
      overlays.default = import ./nix/overlay.nix;

    } // (flake-utils.lib.eachDefaultSystem (system: let

      # First get the package set for this system architecture.
      pkgs = import nixpkgs {
        inherit system;
        # And include our Tahoe-LAFS package in that package set.
        overlays = [ self.overlays.default ];
      };

      # Find out what Python versions we're working with.
      pythonVersions = builtins.attrNames (
        pkgs.lib.attrsets.filterAttrs
          # Match attribute names that look like a Python derivation - CPython
          # or PyPy.  We take care to avoid things like "python-foo" and
          # "python3Full-unittest" though.  We only want things like "pypy38"
          # or "python311".
          (name: _: null != builtins.match "(python|pypy)3[[:digit:]]{0,2}" name)
          pkgs
      );

      # An element of pythonVersions which we'll use for the default package.
      defaultPyVersion = "python310";

      # Retrieve the actual Python package for each configured version.  We
      # already applied our overlay to pkgs so our packages will already be
      # available.
      pythons = builtins.map (pyVer: pkgs.${pyVer}) pythonVersions;

      # string -> string
      #
      # Construct the Tahoe-LAFS package name for the given Python runtime.
      packageName = pyVersion: "${pyVersion}-tahoe-lafs";

      # string -> string
      #
      # Construct the unit test application name for the given Python runtime.
      unitTestName = pyVersion: "${pyVersion}-unittest";

      # (string -> a) -> (string -> b) -> string -> attrset a b
      #
      # Make a singleton attribute set from the result of two functions.
      singletonOf = f: g: x: { ${f x} = g x; };

      # Create a derivation that includes a Python runtime, Tahoe-LAFS, and
      # all of its dependencies.
      makeRuntimeEnv = singletonOf packageName makeRuntimeEnv';
      makeRuntimeEnv' = pyVersion: (pkgs.${pyVersion}.withPackages (ps: with ps;
        [ tahoe-lafs ] ++
        tahoe-lafs.passthru.extras.i2p ++
        tahoe-lafs.passthru.extras.tor
      )).overrideAttrs (old: {
        name = packageName pyVersion;
      });

      # Create a derivation that includes a Python runtime, Tahoe-LAFS, and
      # all of its dependencies.
      makeTestEnv = pyVersion: (pkgs.${pyVersion}.withPackages (ps: with ps;
        [ tahoe-lafs ] ++
        tahoe-lafs.passthru.extras.i2p ++
        tahoe-lafs.passthru.extras.tor ++
        tahoe-lafs.passthru.extras.unittest
      )).overrideAttrs (old: {
        name = packageName pyVersion;
      });
    in {
      legacyPackages = pkgs;

      # Define the flake's package outputs.  We'll define one version of the
      # package for each version of Python we could find.  We'll also point
      # the flake's "default" package at one of these somewhat arbitrarily.
      # The package consists of a Python environment with Tahoe-LAFS available
      # to it.
      packages = with pkgs.lib;
        foldr mergeAttrs {} ([
          { default = self.packages.${system}.${packageName defaultPyVersion}; }
        ] ++ (builtins.map makeRuntimeEnv pythonVersions)
        ++ (builtins.map (singletonOf unitTestName makeTestEnv) pythonVersions)
        );

      # Define the flake's app outputs.  We'll define a version of an app for
      # running the test suite for each version of Python we could find.
      # We'll also define a version of an app for running the "tahoe"
      # command-line entrypoint for each version of Python we could find.
      apps =
        let
          # We avoid writeShellApplication here because it has ghc as a
          # dependency but ghc has Python as a dependency and our Python
          # package override triggers a rebuild of ghc which takes a looong
          # time.
          writeScript = name: text:
            let script = pkgs.writeShellScript name text;
            in "${script}";

          # A helper function to define the runtime entrypoint for a certain
          # Python runtime.
          makeTahoeApp = pyVersion: {
            "tahoe-${pyVersion}" = {
              type = "app";
              program =
                writeScript "tahoe"
                  ''
                    ${makeRuntimeEnv' pyVersion}/bin/tahoe "$@"
                  '';
            };
          };

          # A helper function to define the unit test entrypoint for a certain
          # Python runtime.
          makeUnitTestsApp = pyVersion: {
            "${unitTestName pyVersion}" = {
              type = "app";
              program =
                writeScript "unit-tests"
                  ''
                    export TAHOE_LAFS_HYPOTHESIS_PROFILE=ci
                    ${makeTestEnv pyVersion}/bin/python -m twisted.trial "$@"
                  '';
            };
          };
        in
          with pkgs.lib;
          foldr mergeAttrs
            { default = self.apps.${system}."tahoe-python3"; }
            (
              (builtins.map makeUnitTestsApp pythonVersions) ++
              (builtins.map makeTahoeApp pythonVersions)
            );
    }));
}
