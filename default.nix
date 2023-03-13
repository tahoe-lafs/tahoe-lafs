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
  pkgsVersion ? "nixpkgs-22.11" # a string which chooses a nixpkgs from the
                                # niv-managed sources data

, pkgs ? import sources.${pkgsVersion} { } # nixpkgs itself

, pythonVersion ? "python310" # a string choosing the python derivation from
                              # nixpkgs to target

, extras ? [ "tor" "i2p" ] # a list of strings identifying tahoe-lafs extras,
                           # the dependencies of which the resulting package
                           # will also depend on.  Include all of the runtime
                           # extras by default because the incremental cost of
                           # including them is a lot smaller than the cost of
                           # re-building the whole thing to add them.

}:
with pkgs.${pythonVersion}.pkgs;
callPackage ./nix/tahoe-lafs.nix {
  # Select whichever package extras were requested.
  inherit extras;

  # Define the location of the Tahoe-LAFS source to be packaged.  Clean up as
  # many of the non-source files (eg the `.git` directory, `~` backup files,
  # nix's own `result` symlink, etc) as possible to avoid needing to re-build
  # when files that make no difference to the package have changed.
  tahoe-lafs-src = pkgs.lib.cleanSource ./.;

  # pycddl isn't packaged in nixpkgs so supply our own package of it.
  pycddl = callPackage ./nix/pycddl.nix { };
}
