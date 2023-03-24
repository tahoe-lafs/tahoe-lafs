rec {
  # [] -> [] -> []
  #
  # concatenate one list with another.
  concat = a: b: a ++ b;

  # derivation -> string -> [derivation]
  #
  # get a list of derivations representing the dependencies of a Python
  # package "extra".
  extraDeps = drv: extra: drv.passthru.optional-dependencies.${extra};

  # python -> string
  #
  # extract the "python<major><minor>" identifier for the given Python
  # interpreter.
  pythonVersion = python:
    let
      major = "3";
      minor =
        if python.isPy38 then "8"
        else if python.isPy39 then "9"
        else if python.isPy310 then "10"
        else if python.isPy311 then "11"
        else throw "Unknown Python runtime: ${python}";
    in
      "${python.implementation}${major}${minor}";

  # derivation -> [string] -> [derivation]
  #
  # get a list of derivations representing the dependencies of a list of
  # Python package "extras".
  extrasDeps = drv: extras: builtins.concatLists (map (extraDeps drv) extras);

  # python -> python
  #
  # Customize the given Python with our package overrides.
  makePython = python: python.override {
    packageOverrides = import ./python-overrides.nix;
  };

  # pythonPackages -> derivation
  #
  # Make a Tahoe-LAFS Python package for the given Python package set.
  makePackage = ps: ps.callPackage ./tahoe-lafs.nix {
    # Define the location of the Tahoe-LAFS source to be packaged (the same
    # directory as contains this file).  Clean up as many of the non-source
    # files (eg the `.git` directory, `~` backup files, nix's own `result`
    # symlink, etc) as possible to avoid needing to re-build when files that
    # make no difference to the package have changed.
    tahoe-lafs-src = ps.pkgs.lib.cleanSource ../.;
  };

  # [AttrSet a b] -> AttrSet a b
  #
  # Merge the contents of all of the sets in the given list.  Rightmost sets
  # have priority in the case of collisions.
  mergeSets = sets: builtins.foldl' (a: b: a // b) {} sets;

  # (PythonVersion -> string) -> (PythonVersion -> App) -> PythonVersion -> AttrSet string App
  #
  # Make a flake app for the given version of Python.  The name will be a
  # combination of the Python version and the result of `appIdentifier`.  The
  # app itself will be the result of `appDefinition`.
  makeApp = appIdentifier: appDefinition: pythonVersion: {
    "${appIdentifier pythonVersion}" = appDefinition pythonVersion;
  };

  # (PythonVersion -> string) -> (PythonVersion -> app) -> [PythonVersion] -> AttrSet string app
  #
  # Make flake apps for all of the given Python versions.
  makeApps = appIdentifier: appDefinition: pythonVersions:
    mergeSets (builtins.map (makeApp appIdentifier appDefinition) pythonVersions);

  # nixpkgs -> Python -> App
  #
  # Make a flake app that runs the Tahoe-LAFS unit test suite.
  unitTestsApp = pkgs: python: {
    type = "app";
    program =
      let
        app = pkgs.writeShellApplication {
          name = "unit-tests";
          runtimeInputs = [
            (python.withPackages (ps:
              let tahoe-lafs = makePackage ps;
              in
                [ tahoe-lafs ] ++ (extraDeps tahoe-lafs "unit-test")
            ))
          ];
          text = ''
            export TAHOE_LAFS_HYPOTHESIS_PROFILE=ci
            python -m twisted.trial "$@"
          '';
        };
      in
        "${app}/bin/unit-tests";
  };

  # nixpkgs -> Python -> App
  #
  # Make a flake app that runs the Tahoe-LAFS integration test suite.
  integrationTestsApp = pkgs: python: {
    type = "app";
    program =
      let
        app = pkgs.writeShellApplication {
          name = "integration-tests";
          runtimeInputs = [
            (python.withPackages (ps:
              let tahoe-lafs = makePackage ps;
              in
                [ tahoe-lafs ] ++ (extraDeps tahoe-lafs "integration-test")
            ))
          ];
          text = ''
            export TAHOE_LAFS_HYPOTHESIS_PROFILE=ci
            python -m pytest --timeout=1800 -s -v "$@"
          '';
        };
      in
        "${app}/bin/integration-tests";
  };

  # python -> App
  #
  # Make a flake app that runs the Tahoe-LAFS CLI.
  tahoeApp = python: {
    type = "app";
    program = "${makePackage python.pkgs}/bin/tahoe";
  };
}
