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

      lib = import ./lib.nix {
        inherit system pkgs mach-nix;
      };
      inherit (lib) packageForVersions devShellForVersions withDefault;

      pkgs = nixpkgs.legacyPackages.${system};

      # the Python version used by the default package
      defaultPythonVersion = "python37";

      # the Python versions for which packages are available
      supportedPythonVersions = ["python37" "python38" "python39" "python310" ];

      # the extras we will include in all packages
      extras = [ "tor" "i2p" ];

    in rec {
      version = builtins.substring 0 8 self.lastModifiedDate;

      packages =
        withDefault
          (packageForVersions extras supportedPythonVersions)
          defaultPythonVersion;

      apps.default = {
        type = "app";
        program = "${self.packages.${system}.default}/bin/tahoe";
      };

      devShells =
        withDefault
          (devShellForVersions extras supportedPythonVersions)
          defaultPythonVersion;
    });
}
