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
  # We would like to know the test requirements but mach-nix does not directly
  # expose this information to us.  However, it is perfectly capable of
  # determining it if we ask right...  This is probably not meant to be a
  # public mach-nix API but we pinned mach-nix so we can deal with mach-nix
  # upgrade breakage in our own time.
  mach-lib = import "${sources.mach-nix}/mach_nix/nix/lib.nix" {
    inherit pkgs;
    lib = pkgs.lib;
  };
  tests_require = (mach-lib.extract "python37" ./. "extras_require" ).extras_require.test;

  # Get the Tahoe-LAFS package itself.  This does not include test
  # requirements and we don't ask for test requirements so that we can just
  # re-use the normal package if it is already built.
  tahoe-lafs = import ./. args;

  # If we want to get tahoe-lafs into a Python environment with a bunch of
  # *other* Python modules and let them interact in the usual way then we have
  # to ask mach-nix for tahoe-lafs and those other Python modules in the same
  # way - i.e., using `requirements`.  The other tempting mechanism,
  # `packagesExtra`, inserts an extra layer of Python environment and prevents
  # normal interaction between Python modules (as well as usually producing
  # file collisions in the packages that are both runtime and test
  # dependencies).  To get the tahoe-lafs we just built into the environment,
  # put it into nixpkgs using an overlay and tell mach-nix to get tahoe-lafs
  # from nixpkgs.
  overridesPre = [(self: super: { inherit tahoe-lafs; })];
  providers = tahoe-lafs.meta.mach-nix.providers // { tahoe-lafs = "nixpkgs"; };

  # Make the Python environment in which we can run the tests.
  python-env = mach-nix.mkPython {
    # Get the packaging fixes we already know we need from putting together
    # the runtime package.
    inherit (tahoe-lafs.meta.mach-nix) _;
    # Share the runtime package's provider configuration - combined with our
    # own that causes the right tahoe-lafs to be picked up.
    inherit providers overridesPre;
    requirements = ''
      # Here we pull in the Tahoe-LAFS package itself.
      tahoe-lafs

      # Unfortunately mach-nix misses all of the Python dependencies of the
      # tahoe-lafs satisfied from nixpkgs.  Drag them in here.  This gives a
      # bit of a pyrrhic flavor to the whole endeavor but maybe mach-nix will
      # fix this soon.
      #
      # https://github.com/DavHau/mach-nix/issues/123
      # https://github.com/DavHau/mach-nix/pull/386
      ${tahoe-lafs.requirements}

      # And then all of the test-only dependencies.
      ${builtins.concatStringsSep "\n" tests_require}

      # txi2p-tahoe is another dependency with an environment marker that
      # mach-nix doesn't automatically pick up.
      txi2p-tahoe
    '';
  };
in
# Make a derivation that runs the unit test suite.
pkgs.runCommand "tahoe-lafs-tests" { } ''
  export TAHOE_LAFS_HYPOTHESIS_PROFILE=ci
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
