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

    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils, ... }:
    {
      # This overlay adds Tahoe-LAFS to all of the Python package sets (at
      # least, all of the ones it knows about).  It also pulls in any extra
      # Tahoe-LAFS Python dependencies that are missing and customizes some
      # Python packages to make the overall experience better (faster, not
      # broken, etc).
      overlays.default = import ./nix/overlay.nix;
    } // flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ self.overlays.default ];
        };
      in {
        packages = {
          inherit (pkgs) python311;
        };
      }
    );
}
