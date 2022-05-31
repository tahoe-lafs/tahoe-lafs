{
  description = "Tahoe-LAFS, free and open decentralized data store";
  inputs.nixpkgs = {
    url = "github:NixOS/nixpkgs?rev=838eefb4f93f2306d4614aafb9b2375f315d917f";
    hash = "sha256:1bm8cmh1wx4h8b4fhbs75hjci3gcrpi7k1m1pmiy3nc0gjim9vkg";
  };
  inputs.flake-utils.url = "github:numtide/flake-utils";
  inputs.mach-nix = {
    flake = false;
    url = "github:DavHau/mach-nix?rev=bdc97ba6b2ecd045a467b008cff4ae337b6a7a6b";
    hash = "sha256:12b3jc0g0ak6s93g3ifvdpwxbyqx276k1kl66bpwz8a67qjbcbwf";
  };

  outputs = { self, nixpkgs, flake-utils, mach-nix }:
    flake-utils.lib.eachDefaultSystem (system: let

      pkgs = nixpkgs.legacyPackages.${system};

      pypiDataRev = "76b8f1e44a8ec051b853494bcf3cc8453a294a6a";
      pypiDataSha256 = "sha256:18fgqyh4z578jjhk26n1xi2cw2l98vrqp962rgz9a6wa5yh1nm4x";

      # The Python version used by the default package
      defaultPythonVersion = "python37";

      # The Python versions for which packages are available
      supportedPythonVersions = ["python37" "python38" "python39" "python310" ];

      # Construct the name of the package for a given version of Python
      # str -> str
      packageName = pythonVersion: "tahoe-lafs-${pythonVersion}";

      # Create packages for all of the given Python versions.  The mach-nix
      # derivation to use to build the packages is the 2nd argument.
      #
      # nixpkgs -> derivation -> [str] -> set
      packageForVersions = pkgs: mach-nix-src: pythonVersions: builtins.foldl' (
        accum: pyVersion: accum // {
          ${packageName pyVersion} = pkgs.callPackage ./default.nix {
            mach-nix = pkgs.callPackage mach-nix-src {
              inherit pkgs pypiDataRev pypiDataSha256;
              python = pyVersion;
            };
          };
        }) {} pythonVersions;

    in rec {
      version = builtins.substring 0 8 self.lastModifiedDate;

      packages =
        let
          p = packageForVersions pkgs mach-nix supportedPythonVersions;
        in
          p // { default = p.${packageName defaultPythonVersion}; };

      apps = {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/tahoe";
        };
      };

      devShell = pkgs.mkShell {
        buildInputs = with pkgs; [
          python310
          python39
          python38
          python37
        ];
      };
    });
}
