{
  description = "Tahoe-LAFS, free and open decentralized data store";
  inputs.nixpkgs = {
    url = "github:NixOS/nixpkgs?rev=838eefb4f93f2306d4614aafb9b2375f315d917f";
  };
  inputs.flake-utils.url = "github:numtide/flake-utils";
  inputs.mach-nix = {
    flake = true;
    url = "github:DavHau/mach-nix?rev=bdc97ba6b2ecd045a467b008cff4ae337b6a7a6b";
    inputs = {
      pypi-deps-db = {
        flake = false;
        url = "github:DavHau/pypi-deps-db?rev=76b8f1e44a8ec051b853494bcf3cc8453a294a6a";
      };
      nixpkgs.follows = "nixpkgs";
      flake-utils.follows = "flake-utils";
    };
  };

  outputs = { self, nixpkgs, flake-utils, mach-nix }:
    flake-utils.lib.eachDefaultSystem (system: let

      pkgs = nixpkgs.legacyPackages.${system};

      # The Python version used by the default package
      defaultPythonVersion = "python37";

      # The Python versions for which packages are available
      supportedPythonVersions = ["python37" "python38" "python39" "python310" ];

      # Construct the name of the package for a given version of Python
      # str -> str
      packageName = pythonVersion: "tahoe-lafs-${pythonVersion}";

      packageForVersion = pkgs: extras: pyVersion:
        pkgs.callPackage ./default.nix {
          inherit extras;
          inherit (mach-nix.lib.${system}) buildPythonPackage;
          pythonVersion = pyVersion;
        };

      # Create packages for all of the given Python versions.
      #
      # nixpkgs -> derivation -> [str] -> set
      packageForVersions = pkgs: pythonVersions:
        let
          mkPackage = packageForVersion pkgs [ "tor" "i2p" ];
        in
          builtins.foldl' (accum: pyVersion: accum // {
            ${packageName pyVersion} = mkPackage pyVersion;
          }) {} pythonVersions;

      devShellForVersion = pkgs: extras: pyVersion:
        pkgs.mkShell {
          shellHook = "export PYTHONPATH=\${PWD}/src:\${PYTHONPATH}";
          buildInputs = [ (devPy pkgs extras pyVersion) ];
        };

      devShellForVersions = pkgs: pythonVersions:
        let
          mkPackage = devShellForVersion pkgs [ "tor" "i2p" "test" ];
        in
          builtins.foldl' (accum: pyVersion: accum // {
            ${packageName pyVersion} = mkPackage pyVersion;
          }) {} pythonVersions;

      devPy = pkgs: extras: pythonVersion:
        mach-nix.lib.${system}.mkPython {
          requirements = requirementsForVersion pkgs extras pythonVersion;
        };

      requirementsForVersion = pkgs: extras: pyVersion: (
        packageForVersion pkgs extras pyVersion
      ).requirements;

    in rec {
      version = builtins.substring 0 8 self.lastModifiedDate;

      packages =
        let
          p = packageForVersions pkgs supportedPythonVersions;
        in
          p // { default = p.${packageName defaultPythonVersion}; };

      apps.default = {
        type = "app";
        program = "${self.packages.${system}.default}/bin/tahoe";
      };

      devShells =
        let
          s = devShellForVersions pkgs supportedPythonVersions;
        in
          s // { default = s.${packageName defaultPythonVersion}; };
    });
}
