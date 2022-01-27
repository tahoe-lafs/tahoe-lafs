let
  sources = import nix/sources.nix;
in
# See default.nix for documentation about parameters.
{ pkgsVersion ? "nixpkgs-21.11"
, pkgs ? import sources.${pkgsVersion} { }
, pypiData ? sources.pypi-deps-db
, pythonVersion ? "python37"
, mach-nix ? import sources.mach-nix {
    inherit pkgs pypiData;
    python = pythonVersion;
  }
}@args:
let
  # Get the package with all of its test requirements.
  tahoe-lafs = import ./. (args // { extras = [ "test" ]; });

  # Put it into a Python environment.
  python-env = pkgs.${pythonVersion}.withPackages (ps: [
    tahoe-lafs
  ]);
in
# Make a derivation that runs the unit test suite.
pkgs.runCommand "tahoe-lafs-tests" { } ''
  ${python-env}/bin/python -m twisted.trial -j $NIX_BUILD_CORES allmydata
''
