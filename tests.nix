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
  python-env = mach-nix.mkPython {
    inherit (tahoe-lafs.meta.mach-nix) providers _;
    packagesExtra = [ tahoe-lafs ];
    requirements = ''
    # txi2p-tahoe is another dependency with an environment marker that
    # mach-nix doesn't automatically pick up.
    txi2p-tahoe
    '';
  };
in
# Make a derivation that runs the unit test suite.
pkgs.runCommand "tahoe-lafs-tests" { } ''
  ${python-env}/bin/python -m twisted.trial -j $NIX_BUILD_CORES allmydata

  # It's not cool to put the whole _trial_temp into $out because it has weird
  # files in it we don't want in the store.  Plus, even all of the less weird
  # files are mostly just trash that's not meaningful if the test suite passes
  # (which is the only way we get $out anyway).
  #
  # The build log itself is typically available from `nix-store --read-log` so
  # we don't need to record that either.
  echo "passed" >$out

''
