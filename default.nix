let
  # sources.nix contains information about which versions of some of our
  # dependencies we should use.  since we use it to pin nixpkgs, all the rest
  # of our dependencies are *also* pinned - indirectly.
  #
  # sources.nix is managed using a tool called `niv`.  as an example, to
  # update to the most recent version of nixpkgs from the 21.11 maintenance
  # release, in the top-level tahoe-lafs checkout directory you run:
  #
  #   niv update nixpkgs-21.11
  #
  # niv also supports chosing a specific revision, following a different
  # branch, etc.  find complete documentation for the tool at
  # https://github.com/nmattia/niv
  sources = import nix/sources.nix;
in
{
  pkgsVersion ? "nixpkgs-22.11" # a string which chooses a nixpkgs from the
                                # niv-managed sources data

, pkgs ? import sources.${pkgsVersion} { # nixpkgs itself
  overlays = [ (import ./nix/overlay.nix) ];
}

, pythonVersion ? "python310" # a string choosing the python derivation from
                              # nixpkgs to target
}:
pkgs.${pythonVersion}.withPackages (ps: [ ps.tahoe-lafs ])
