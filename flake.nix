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

  # Provide a Python package override that adds a working Tahoe-LAFS to the
  # package set.
  #
  # "Python package override" is not a formally recognized kind of flake
  # output but it provides the best results here:
  #
  #  1. It composes.  It is possible to apply N package overrides to a Python
  #     derivation to produce a new one that integrates them all.
  #
  #     1b. It avoids creating a combinatorial explosion of outputs.  We don't
  #         need to provide a Tahoe-LAFS package for every version of Python.
  #         Downstream consumers can apply the override to whichever Python
  #         derivation they like.
  #
  #     1c. It avoids locking downstream consumers into some *specific* Python
  #         derivations (the ones we pick).  They can bring their own.
  #
  #  2. It avoids unnecessary rebuilds.  It is an override but by itself it
  #     does not override anything in nixpkgs.  This means it does not impact
  #     any derivations that depend on Python itself.  Since nixpkgs includes
  #     many such derivations and many of them are expensive to build, this is
  #     a significant advantage.
  #
  #     2b. Maybe it is actually possible to avoid these rebuilds if we
  #     override python in nixpkgs.  Study
  #     https://github.com/SomeoneSerge/pkgs/blob/0b9e433013b37b71f358dd5dfec00a96ca0dab7e/overlay.nix#L21-L27
  #     and see if you can make any sense of what's going on there.
  #
  outputs = { self, nixpkgs, flake-utils, ... }:
    {
      # An overlay to add Tahoe-LAFS to the Python package sets.
      overlays.default = import ./nix/overlay.nix { inherit (nixpkgs) lib; };
    } // flake-utils.lib.eachSystem [ "x86_64-linux" ] (system:
      {
        packages =
          let
            pkgs = import nixpkgs {
              inherit system;
              overlays = [ (import ./nix/overlay.nix { inherit (nixpkgs) lib; }) ];
            };
            # Create a Python runtime with the Tahoe-LAFS package installed.
            makeTahoe = py: py.withPackages (ps: [ps.tahoe-lafs]);
          in
            with pkgs; {
              # Okay, above I said we would avoid a combinatorial explosion
              # but here we have a Tahoe-LAFS package for every Python
              # derivation.
              #
              # Both things are true.  These packages let us test that our
              # package actually works against these different versions of
              # Python but downstream consumers can largely ignore them and
              # pick their own Python derivation (of course, we will not have
              # tested that such an integration works but at least this gives
              # us a good idea about which major/minor versions of which
              # Python interpreters Tahoe-LAFS works with).
              #
              # Also note that these are not *Python packages*.  They are
              # whole Python runtimes which happen to have Tahoe-LAFS
              # available to them.
              python-tahoe-lafs = makeTahoe python;
              python3-tahoe-lafs = makeTahoe python3;

              python38-tahoe-lafs = makeTahoe python38;
              python39-tahoe-lafs = makeTahoe python39;
              python310-tahoe-lafs = makeTahoe python310;
              python311-tahoe-lafs = makeTahoe python311;

              pypy38-tahoe-lafs = makeTahoe pypy38;
              pypy39-tahoe-lafs = makeTahoe pypy39;
            };
      }
    );
}
